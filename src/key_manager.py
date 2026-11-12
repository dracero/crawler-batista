"""Gestor de rotación y respaldo (round-robin + cooldown) para múltiples API keys de Google Gemini."""

import os
import re
import time
import logging
import threading
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES = ("tu_api_key", "your_api_key", "changeme", "xxx")


def _parse_key_list(raw_value: str) -> List[str]:
    """Divide una cadena con múltiples API keys separadas por coma, punto y coma o salto de línea."""
    if not raw_value:
        return []
    parts = re.split(r"[,;\n\r]+", raw_value)
    return [p.strip() for p in parts if p.strip()]


def _is_valid_key_candidate(value: str) -> bool:
    """Descarta valores vacíos o placeholders de ejemplo."""
    if not value:
        return False
    lowered = value.lower()
    return not any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def get_gemini_keys() -> List[str]:
    """Obtiene todas las API keys configuradas en variables de entorno, sin duplicados.

    Soporta:
    - GEMINI_API_KEYS (lista separada por comas/líneas)
    - GEMINI_API_KEY / GOOGLE_API_KEY (una o varias separadas por comas)
    - GEMINI_API_KEY_1, GEMINI_API_KEY_2, ... (variables numeradas)
    """
    candidates: List[str] = []

    candidates.extend(_parse_key_list(os.getenv("GEMINI_API_KEYS", "")))
    candidates.extend(_parse_key_list(os.getenv("GEMINI_API_KEY", "")))
    candidates.extend(_parse_key_list(os.getenv("GOOGLE_API_KEY", "")))

    numbered_vars: List[Tuple[int, str]] = []
    for env_name, env_val in os.environ.items():
        match = re.fullmatch(r"(?:GEMINI|GOOGLE)_API_KEY_(\d+)", env_name)
        if match and env_val:
            numbered_vars.append((int(match.group(1)), env_val))

    for _, val in sorted(numbered_vars, key=lambda item: item[0]):
        candidates.extend(_parse_key_list(val))

    seen = set()
    unique_keys: List[str] = []
    for key in candidates:
        if _is_valid_key_candidate(key) and key not in seen:
            seen.add(key)
            unique_keys.append(key)

    return unique_keys


def mask_key(api_key: str) -> str:
    """Devuelve una versión enmascarada segura de la API key para logs."""
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


class GeminiKeyRotator:
    """Rotador thread-safe de API keys con round-robin y enfriamiento (cooldown) ante cuotas agotadas."""

    def __init__(self, default_cooldown_seconds: float = 60.0):
        self._lock = threading.Lock()
        self._keys: Tuple[str, ...] = ()
        self._cursor: int = 0
        self._cooldowns: Dict[str, float] = {}
        self._invalid_keys: set[str] = set()
        self.default_cooldown_seconds = default_cooldown_seconds

    def _sync_keys_locked(self) -> None:
        """Sincroniza la lista de keys con el entorno actual."""
        current = tuple(get_gemini_keys())
        if current != self._keys:
            self._keys = current
            self._cursor = 0
            # Limpiar estado de keys que ya no están configuradas
            valid_set = set(current)
            self._cooldowns = {k: v for k, v in self._cooldowns.items() if k in valid_set}
            self._invalid_keys = {k for k in self._invalid_keys if k in valid_set}

    def next_ordered_keys(self, include_cooling: bool = False) -> List[Tuple[int, str]]:
        """Devuelve keys disponibles fuera de cooldown en orden round-robin (circuito abierto si todas enfrían)."""
        with self._lock:
            self._sync_keys_locked()
            n = len(self._keys)
            if n == 0:
                return []

            now = time.monotonic()
            # Expirar cooldowns cumplidos
            expired = [k for k, until in self._cooldowns.items() if now >= until]
            for k in expired:
                del self._cooldowns[k]

            start = self._cursor % n
            self._cursor = (self._cursor + 1) % n

            healthy: List[Tuple[int, str]] = []
            cooling: List[Tuple[float, int, str]] = []

            for offset in range(n):
                idx = (start + offset) % n
                key = self._keys[idx]
                if key in self._invalid_keys:
                    continue
                if key in self._cooldowns:
                    cooling.append((self._cooldowns[key], idx, key))
                else:
                    healthy.append((idx + 1, key))

            if healthy:
                return healthy
            if include_cooling:
                cooling.sort(key=lambda item: item[0])
                return [(idx + 1, key) for _, idx, key in cooling]
            return []

    def report_rate_limit(self, api_key: str, cooldown_seconds: float | None = None) -> None:
        """Pone temporalmente en cooldown una key que alcanzó su límite de cuota o peticiones (429)."""
        wait_s = cooldown_seconds if cooldown_seconds is not None else float(
            os.getenv("GEMINI_KEY_COOLDOWN_SECONDS", self.default_cooldown_seconds)
        )
        with self._lock:
            self._cooldowns[api_key] = time.monotonic() + max(1.0, wait_s)
        logger.warning(f"API key {mask_key(api_key)} en cooldown por {wait_s:.0f}s tras límite de cuota.")

    def report_invalid(self, api_key: str) -> None:
        """Marca una key como inválida para no reutilizarla durante la sesión."""
        with self._lock:
            self._invalid_keys.add(api_key)
        logger.error(f"API key {mask_key(api_key)} rechazada por autenticación (401/403); excluida de la rotación.")

    def report_success(self, api_key: str) -> None:
        """Limpia el cooldown de una key si respondió correctamente."""
        with self._lock:
            self._cooldowns.pop(api_key, None)

    def get_status(self) -> Dict[str, Any]:
        """Devuelve métricas del pool de keys sin exponer secretos."""
        with self._lock:
            self._sync_keys_locked()
            now = time.monotonic()
            active = 0
            cooling = 0
            invalid = 0
            for key in self._keys:
                if key in self._invalid_keys:
                    invalid += 1
                elif key in self._cooldowns and now < self._cooldowns[key]:
                    cooling += 1
                else:
                    active += 1
            return {
                "total_keys": len(self._keys),
                "active_keys": active,
                "cooldown_keys": cooling,
                "invalid_keys": invalid,
            }


_rotator = GeminiKeyRotator()


def get_key_rotator() -> GeminiKeyRotator:
    """Obtiene la instancia global del rotador de API keys."""
    return _rotator


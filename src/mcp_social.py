"""
Módulo MCP (Model Context Protocol) para Redes Sociales: X (Twitter), Instagram y Facebook.

Provee:
1. Herramientas MCP estandarizadas (`search_x_mcp`, `search_instagram_mcp`, `search_facebook_mcp`, `search_all_social_mcp`)
   que pueden ejecutarse tanto directamente desde el grafo de agentes (LangGraph) como exponerse vía protocolo MCP JSON-RPC/stdio.
2. Decodificación matemática de fechas exactas desde:
   - IDs Snowflake de X/Twitter (`/status/<id>`)
   - Shortcodes Base64URL de Instagram (`/p/<code>/` y `/reel/<code>/`)
   - IDs de video de TikTok (`/video/<id>`)
   - Prefijos de fecha en fragmentos de Facebook, Instagram y X (`Oct 28, 2026`, `3 days ago`, etc.)
3. Enriquecimiento opcional de tweets de X mediante la API pública de FxTwitter (texto completo, autor, likes, retweets, fecha exacta).
"""

import os
import re
import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional

from src.state import SearchResult

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

INSTAGRAM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

SPANISH_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "ene": "01", "abr": "04", "ago": "08", "dic": "12",
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "Mayo": "05", "mayo": "05",
    "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12"
}


def decode_x_snowflake_date(url: str) -> Optional[str]:
    """Extrae la fecha y hora exacta desde el Snowflake ID de un status de X (Twitter)."""
    m = re.search(r"status/(\d{15,22})", url or "")
    if not m:
        return None
    try:
        sf = int(m.group(1))
        ts_sec = ((sf >> 22) + 1288834974657) / 1000.0
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return None


def decode_instagram_shortcode_date(url: str) -> Optional[str]:
    """Decodifica la fecha exacta de publicación desde el shortcode Base64URL de un post o reel de Instagram."""
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels)/([A-Za-z0-9_-]{8,14})", url or "")
    if not m:
        return None
    code = m.group(1)[:11]
    try:
        media_id = 0
        for ch in code:
            if ch not in INSTAGRAM_ALPHABET:
                return None
            media_id = (media_id * 64) + INSTAGRAM_ALPHABET.index(ch)
        ts_ms = (media_id >> 23) + 1314220021721
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return None


def decode_tiktok_video_date(url: str) -> Optional[str]:
    """Decodifica la fecha exacta desde el ID de video de TikTok."""
    m = re.search(r"video/(\d{15,22})", url or "")
    if not m:
        return None
    try:
        vid_id = int(m.group(1))
        ts_sec = vid_id >> 32
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return None


def parse_snippet_date(snippet: str) -> Optional[str]:
    """Extrae fechas desde el inicio del texto/snippet (ej: 'Oct 28, 2026 ·', '3 days ago ·', '21 hours ago ·')."""
    if not snippet:
        return None
    head = snippet[:60].strip()
    now = datetime.now()

    # Patrón relativo en inglés/español: "3 days ago", "21 hours ago", "hace 2 días"
    m_rel = re.search(r"(\d+)\s+(hour|hours|day|days|week|weeks|minute|minutes|hora|horas|día|días|dia|dias)\s*(?:ago|atrás)?", head, re.IGNORECASE)
    if m_rel:
        val = int(m_rel.group(1))
        unit = m_rel.group(2).lower()
        if "hour" in unit or "hora" in unit:
            dt = now - timedelta(hours=val)
            return dt.strftime("%d/%m/%Y %H:%M")
        elif "day" in unit or "día" in unit or "dia" in unit:
            dt = now - timedelta(days=val)
            return dt.strftime("%d/%m/%Y")
        elif "week" in unit:
            dt = now - timedelta(weeks=val)
            return dt.strftime("%d/%m/%Y")
        elif "min" in unit:
            dt = now - timedelta(minutes=val)
            return dt.strftime("%d/%m/%Y %H:%M")

    # Patrón "Oct 28, 2026" o "Mar 25, 2026"
    m_en = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})\b", head)
    if m_en:
        mon_str = m_en.group(1).lower()[:3]
        day_str = m_en.group(2).zfill(2)
        year_str = m_en.group(3)
        mon_num = SPANISH_MONTHS.get(mon_str)
        if mon_num:
            return f"{day_str}/{mon_num}/{year_str}"

    # Patrón "28 oct 2026" o "28 de octubre de 2026"
    m_es = re.search(r"\b(\d{1,2})\s+(?:de\s+)?([A-Za-z]{3,10})\s+(?:de\s+)?(20\d{2})\b", head)
    if m_es:
        day_str = m_es.group(1).zfill(2)
        mon_str = m_es.group(2).lower()
        year_str = m_es.group(3)
        mon_num = SPANISH_MONTHS.get(mon_str) or SPANISH_MONTHS.get(mon_str[:3])
        if mon_num:
            return f"{day_str}/{mon_num}/{year_str}"

    return None


def extract_and_format_date(raw_date: Optional[str], url: str = "", snippet: str = "") -> str:
    """
    Normaliza cualquier fecha de RSS, DuckDuckGo, URL de red social o snippet a formato legible 'DD/MM/YYYY' (o con hora).
    Garantiza que ninguna noticia o posteo quede sin fecha.
    """
    # 1. Intentar decodificación matemática desde URL de X, Instagram o TikTok
    url_date = (
        decode_x_snowflake_date(url)
        or decode_instagram_shortcode_date(url)
        or decode_tiktok_video_date(url)
    )
    if url_date:
        return url_date

    # 2. Intentar parsear raw_date (RFC-2822 de RSS o ISO-8601 de DDG)
    if raw_date:
        raw_clean = raw_date.strip()
        # Si ya está en formato DD/MM/YYYY
        if re.match(r"^\d{2}/\d{2}/\d{4}", raw_clean):
            return raw_clean
        # RFC 2822 (ej: "Fri, 06 Nov 2026 14:23:00 GMT")
        try:
            dt = parsedate_to_datetime(raw_clean)
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass
        # ISO 8601 (ej: "2026-11-06T14:23:00+00:00" o "2026-11-06")
        try:
            iso_part = raw_clean.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_part)
            if dt.hour == 0 and dt.minute == 0:
                return dt.strftime("%d/%m/%Y")
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass
        # Verificar si contiene fecha tipo "Oct 28, 2026"
        parsed_raw = parse_snippet_date(raw_clean)
        if parsed_raw:
            return parsed_raw

    # 3. Intentar extraer desde el prefijo del snippet ("Oct 28, 2026 · ...", "3 days ago · ...")
    snippet_date = parse_snippet_date(snippet)
    if snippet_date:
        return snippet_date

    # 4. Respaldo con fecha de relevamiento actual
    return datetime.now().strftime("%d/%m/%Y")


def enrich_x_tweet_via_fxtwitter(url: str) -> Optional[Dict[str, Any]]:
    """Consulta la API pública de FxTwitter para obtener el texto completo, fecha exacta y métricas de un tweet."""
    m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d{15,22})", url or "")
    if not m:
        return None
    user, status_id = m.group(1), m.group(2)
    api_url = f"https://api.fxtwitter.com/{user}/status/{status_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "UBANewsMonitor-MCP/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tw = data.get("tweet")
            if tw and tw.get("text"):
                author_screen = tw.get("author", {}).get("screen_name") or user
                likes = tw.get("likes", 0)
                rts = tw.get("retweets", 0)
                text = tw["text"].strip()
                date_str = decode_x_snowflake_date(url) or extract_and_format_date(tw.get("created_at"), url)
                return {
                    "title": f"@{author_screen} en X: {text[:100]}{'...' if len(text) > 100 else ''}",
                    "snippet": f"{text} (❤️ {likes} likes · 🔁 {rts} RTs)",
                    "source": f"X (@{author_screen})",
                    "date": date_str
                }
    except Exception:
        pass
    return None


def search_x_mcp(query: str, max_items: int = 4) -> List[SearchResult]:
    """Herramienta MCP para buscar publicaciones en X (Twitter) con fecha y usuario."""
    results: List[SearchResult] = []
    search_q = f"{query} \"Universidad de Buenos Aires\" site:x.com"
    try:
        with DDGS(timeout=7) as ddgs:
            raw = list(ddgs.text(search_q, timelimit="m", max_results=max_items + 2))
            if not raw:
                raw = list(ddgs.text(search_q, max_results=max_items + 2))
            for r in raw:
                href = (r.get("href") or "").strip()
                if "x.com/" not in href.lower() and "twitter.com/" not in href.lower():
                    continue
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if not title and not body:
                    continue

                # Si es un status directo, intentar enriquecer con FxTwitter
                enriched = enrich_x_tweet_via_fxtwitter(href) if "/status/" in href else None
                if enriched:
                    results.append(SearchResult(
                        title=enriched["title"],
                        url=href,
                        snippet=enriched["snippet"],
                        date=enriched["date"],
                        source=enriched["source"],
                        source_type="red_social"
                    ))
                else:
                    m_user = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", href)
                    handle = m_user.group(1) if m_user else ""
                    src_label = f"X (@{handle})" if handle and handle.lower() not in ("search", "i", "home") else "X (Twitter)"
                    clean_body = re.sub(r"^[A-Za-z0-9\s,]+\s*·\s*", "", body).strip()
                    display_title = title if len(title) > 25 and "/ X" not in title else f"{src_label}: {clean_body[:100]}"
                    date_str = extract_and_format_date(None, href, body)
                    results.append(SearchResult(
                        title=display_title,
                        url=href,
                        snippet=clean_body or title,
                        date=date_str,
                        source=src_label,
                        source_type="red_social"
                    ))
                if len(results) >= max_items:
                    break
    except Exception as e:
        logger.debug(f"Aviso en MCP X: {e}")
    return results


def search_instagram_mcp(query: str, max_items: int = 4) -> List[SearchResult]:
    """Herramienta MCP para buscar posts y reels en Instagram con fecha decodificada desde el shortcode."""
    results: List[SearchResult] = []
    search_q = f"{query} \"Universidad de Buenos Aires\" (site:instagram.com/p/ OR site:instagram.com/reel/)"
    try:
        with DDGS(timeout=7) as ddgs:
            raw = list(ddgs.text(search_q, max_results=max_items + 2))
            if not raw:
                raw = list(ddgs.text(f"{query} \"Universidad de Buenos Aires\" site:instagram.com", max_results=max_items + 2))
            for r in raw:
                href = (r.get("href") or "").strip()
                if "instagram.com/" not in href.lower():
                    continue
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if not title and not body:
                    continue

                clean_body = re.sub(r"^[A-Za-z0-9\s,]+\s*·\s*", "", body).strip()
                date_str = extract_and_format_date(None, href, body)

                if title.lower() in ("instagram", "") or len(title) < 20:
                    display_title = f"Instagram UBA: {clean_body[:105]}"
                else:
                    display_title = title

                src_label = "Instagram (@uba_ar)" if "uba" in display_title.lower() or "universidad de buenos aires" in display_title.lower() else "Instagram"
                results.append(SearchResult(
                    title=display_title,
                    url=href,
                    snippet=clean_body or title,
                    date=date_str,
                    source=src_label,
                    source_type="red_social"
                ))
    except Exception as e:
        logger.debug(f"Aviso en MCP Instagram: {e}")

    # Ordenar publicaciones de Instagram de más reciente a más antigua según su fecha DD/MM/YYYY
    def _date_sort_key(item: SearchResult) -> str:
        d = item.date or ""
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", d)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return ""

    results.sort(key=_date_sort_key, reverse=True)
    return results[:max_items]


def search_facebook_mcp(query: str, max_items: int = 4) -> List[SearchResult]:
    """Herramienta MCP para buscar publicaciones en Facebook filtrando anuncios y extrayendo fechas."""
    results: List[SearchResult] = []
    search_q = f"{query} \"Universidad de Buenos Aires\" site:facebook.com"
    try:
        with DDGS(timeout=7) as ddgs:
            raw = list(ddgs.text(search_q, timelimit="m", max_results=max_items + 3))
            if not raw:
                raw = list(ddgs.text(search_q, max_results=max_items + 3))
            for r in raw:
                href = (r.get("href") or "").strip()
                # Filtrar estrictamente dominios de facebook.com (evita anuncios de bing.com/aclick)
                if "facebook.com/" not in href.lower():
                    continue
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if not title and not body:
                    continue

                date_str = extract_and_format_date(None, href, body)
                clean_body = re.sub(r"^[A-Za-z0-9\s,]+\s*·\s*", "", body).strip()
                first_title_part = title.split("- Facebook")[0].strip() if "- Facebook" in title else title
                if len(first_title_part) < 35 and clean_body:
                    display_title = f"{first_title_part}: {clean_body[:100]}"
                else:
                    display_title = first_title_part or clean_body[:100]

                m_page = re.search(r"facebook\.com/([A-Za-z0-9_.]+)", href)
                page_name = m_page.group(1) if m_page and m_page.group(1).lower() not in ("groups", "watch", "share", " events") else ""
                src_label = f"Facebook ({page_name})" if page_name else "Facebook"

                results.append(SearchResult(
                    title=display_title,
                    url=href,
                    snippet=clean_body or title,
                    date=date_str,
                    source=src_label,
                    source_type="red_social"
                ))
                if len(results) >= max_items:
                    break
    except Exception as e:
        logger.debug(f"Aviso en MCP Facebook: {e}")
    return results


def search_all_social_mcp(query: str, max_per_platform: int = 3) -> Dict[str, List[SearchResult]]:
    """Ejecuta los conectores MCP de X, Instagram y Facebook y devuelve los resultados organizados por plataforma."""
    return {
        "x": search_x_mcp(query, max_items=max_per_platform),
        "instagram": search_instagram_mcp(query, max_items=max_per_platform),
        "facebook": search_facebook_mcp(query, max_items=max_per_platform),
    }


def get_mcp_social_status() -> Dict[str, Any]:
    """Devuelve el estado de los conectores MCP de Redes Sociales para el endpoint /api/config."""
    return {
        "enabled": True,
        "platforms": ["X (Twitter)", "Instagram", "Facebook", "TikTok", "YouTube", "Reddit"],
        "features": [
            "X/Twitter MCP (Snowflake Date + FxTwitter API)",
            "Instagram MCP (Posts/Reels + Base64 Shortcode Date)",
            "Facebook MCP (Publicaciones + Extractor de Fechas)"
        ]
    }

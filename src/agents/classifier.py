"""Agente Clasificador: Analiza y clasifica noticias y publicaciones en redes sociales de la UBA usando Google Gemini con respaldo automático."""

import os
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Tuple
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from src.state import GraphState, NewsClassification, AnalyzedNews
from src.key_manager import get_key_rotator, mask_key

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Eres un analista de medios, redes sociales y asuntos universitarios especializado en la Universidad de Buenos Aires (UBA) de Argentina.
Tu misión es clasificar noticias periodísticas y publicaciones en redes sociales sobre la UBA evaluando su impacto institucional, académico, presupuestario o social.

Criterios de clasificación:
- BUENA: Logros científicos, subas en rankings internacionales (QS, etc.), premios a investigadores, inauguración de obras, nuevos convenios, avances académicos, reconocimientos o repercusión positiva en redes sociales.
- MALA: Recortes presupuestarios, paros docentes o no docentes, tomas de facultades, deterioro de infraestructura, polémicas por salarios o financiamiento, conflictos gremiales, cancelaciones de programas o reclamos críticos.
- NEUTRAL: Información administrativa rutinaria (fechas de inscripción al CBC, calendario académico, actos protocolares normales, consultas estudiantiles en redes sin controversia ni hito relevante).

Puntuación (score):
- De -1.0 a -0.1 para noticias malas (ej: -0.9 paro total por presupuesto).
- 0.0 para neutrales.
- De +0.1 a +1.0 para noticias buenas (ej: +0.8 UBA en el top 100 mundial).

Debes responder estrictamente en el formato estructurado solicitado."""

CLASSIFIER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", """Por favor analiza la siguiente noticia o publicación en redes sociales:

Título: {title}
Fuente principal: {source}
Cantidad de apariciones detectadas: {occurrences}
Fuentes donde apareció: {sources_str}
Contenido / Fragmentos recopilados:
{content}
""")
])

# Modelo Gemini fijado exclusivamente en versión 3.6
DEFAULT_MODEL = "gemini-3.6-flash"

def rule_based_fallback_classifier(title: str, content: str) -> NewsClassification:
    """Clasificador heurístico de respaldo cuando la API no está disponible."""
    text = (title + " " + content).lower()
    
    bad_keywords = ["paro", "huelga", "recorte", "crisis", "ajuste", "conflicto", "falta de fondos", "desfinanciamiento", "deterioro", "toma", "reclamo", "marcha"]
    good_keywords = ["premio", "ranking", "distinción", "logro", "avance", "inauguración", "descubrimiento", "orgullo", "reconocimiento", "acuerdo", "top 100", "beneficio", "gratuito"]
    
    bad_count = sum(text.count(w) for w in bad_keywords)
    good_count = sum(text.count(w) for w in good_keywords)
    
    if bad_count > good_count:
        return NewsClassification(
            sentiment="MALA",
            score=-0.6,
            category="Conflicto / Presupuesto (Heurística)",
            key_points=["Identificadas palabras clave de conflicto, reclamos o recortes presupuestarios."],
            justification="Clasificación heurística de respaldo: el contenido contiene menciones reiteradas a conflictos o dificultades presupuestarias."
        )
    elif good_count > bad_count:
        return NewsClassification(
            sentiment="BUENA",
            score=0.7,
            category="Logros / Reconocimientos (Heurística)",
            key_points=["Identificadas palabras clave asociadas a reconocimientos y avances."],
            justification="Clasificación heurística de respaldo: el contenido resalta distinciones, reconocimientos o actividades positivas."
        )
    else:
        return NewsClassification(
            sentiment="NEUTRAL",
            score=0.0,
            category="General / Informativa (Heurística)",
            key_points=["Contenido de carácter informativo o consulta general."],
            justification="Clasificación heurística de respaldo: no se detectó un sesgo marcadamente positivo ni negativo."
        )

def _is_quota_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(tok in lowered for tok in ("429", "resource_exhausted", "quota", "rate limit", "too many requests"))


def _is_auth_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(tok in lowered for tok in ("401", "403", "api_key_invalid", "api key not valid", "invalid api key", "permission_denied"))


_client_lock = threading.Lock()
_chain_cache: Dict[Tuple[str, str], Any] = {}


def _get_or_create_chain(model_name: str, api_key: str) -> Any:
    cache_key = (model_name, api_key)
    with _client_lock:
        chain = _chain_cache.get(cache_key)
        if chain is None:
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=0.1,
                max_retries=0,
                timeout=20,
            )
            chain = CLASSIFIER_PROMPT | llm.with_structured_output(NewsClassification)
            _chain_cache[cache_key] = chain
        return chain


def classify_article(
    title: str,
    content: str,
    source: Optional[str] = None,
    occurrences: int = 1,
    sources: Optional[List[str]] = None
) -> NewsClassification:
    """Clasifica una noticia o posteo utilizando Gemini 3.6 rotando API keys y con circuit breaker."""
    rotator = get_key_rotator()
    ordered_keys = rotator.next_ordered_keys()
    if not ordered_keys:
        return rule_based_fallback_classifier(title, content)

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if "3.6" not in model_name:
        logger.warning(f"El modelo '{model_name}' no es versión 3.6. Utilizando '{DEFAULT_MODEL}'.")
        model_name = DEFAULT_MODEL

    trimmed_content = content[:6000] if content else "(Sin contenido adicional)"
    sources_str = ", ".join(sources) if sources else (source or "Desconocido")

    for key_idx, api_key in ordered_keys:
        try:
            chain = _get_or_create_chain(model_name, api_key)
            result: NewsClassification = chain.invoke({
                "title": title or "Sin título",
                "source": source or "Desconocido",
                "occurrences": occurrences,
                "sources_str": sources_str,
                "content": trimmed_content
            })
            rotator.report_success(api_key)
            return result

        except Exception as e:
            err_msg = str(e)
            if _is_auth_error(err_msg):
                rotator.report_invalid(api_key)
                continue
            if _is_quota_error(err_msg):
                rotator.report_rate_limit(api_key)
                continue
            logger.warning(f"Error con Gemini 3.6 y key #{key_idx} ({mask_key(api_key)}): {err_msg}. Rotando clave...")
            continue

    logger.warning("No se pudo clasificar mediante Gemini 3.6 tras rotar todas las claves disponibles. Utilizando clasificador heurístico de respaldo.")
    return rule_based_fallback_classifier(title, content)

def build_combined_content(meta: Dict[str, Any], crawled_markdown: str) -> str:
    """Combina el snippet de búsqueda, los titulares de apariciones múltiples y el texto extraído por el crawler."""
    parts = []
    snippet = meta.get("snippet", "")
    if snippet:
        parts.append(f"Resumen / Bajada: {snippet}")

    mentions = meta.get("mentions", [])
    if len(mentions) > 1:
        mention_lines = [f"- [{m.get('source', 'Fuente')}] {m.get('title', '')}" for m in mentions[:10]]
        parts.append("Cobertura en otros medios y redes:\n" + "\n".join(mention_lines))

    if crawled_markdown:
        parts.append(f"Contenido extraído de la página:\n{crawled_markdown}")

    return "\n\n".join(parts)

def _classify_single_crawled(article: Dict[str, Any], search_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Combina metadatos y Markdown de un artículo y ejecuta la clasificación."""
    url = article["url"]
    meta = search_results.get(url, {})
    # Priorizar el titular limpio obtenido en la búsqueda por sobre títulos genéricos de login/redirección
    title = meta.get("title") or article.get("title") or "Noticia UBA"
    source = meta.get("source", "Medio Web")
    source_type = meta.get("source_type", "medio")
    date = meta.get("date")
    occurrences = meta.get("occurrences", 1)
    sources = meta.get("sources") or ([source] if source else [])
    mentions = meta.get("mentions") or []

    content = build_combined_content(meta, article.get("content_markdown", ""))

    classification = classify_article(
        title=title,
        content=content,
        source=source,
        occurrences=occurrences,
        sources=sources,
    )

    analyzed_item = AnalyzedNews(
        url=url,
        title=title,
        source=source,
        source_type=source_type,
        date=date,
        occurrences=occurrences,
        sources=sources,
        mentions=mentions,
        classification=classification,
    )
    return analyzed_item.model_dump()


def classifier_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Clasificador con concurrencia acotada."""
    search_results = {r["url"]: r for r in state.get("search_results", [])}
    crawled_articles = state.get("crawled_articles", [])

    logger.info(f"Agente Clasificador evaluando {len(crawled_articles)} noticias y publicaciones...")
    if not crawled_articles:
        return {"classified_news": []}

    active_keys = get_key_rotator().get_status().get("active_keys", 0)
    workers = min(4, max(1, active_keys))
    if workers == 1 or len(crawled_articles) == 1:
        analyzed_list = [_classify_single_crawled(article, search_results) for article in crawled_articles]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            analyzed_list = list(executor.map(lambda a: _classify_single_crawled(a, search_results), crawled_articles))

    return {"classified_news": analyzed_list}

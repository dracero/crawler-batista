"""Agente Clasificador: Analiza y clasifica noticias y publicaciones en redes sociales de la UBA usando Google Gemini con respaldo automático."""

import os
import logging
from typing import List, Dict, Any, Optional
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from src.state import GraphState, NewsClassification, AnalyzedNews

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

# Modelos Gemini disponibles
FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash"
]

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

def classify_article(
    title: str,
    content: str,
    source: Optional[str] = None,
    occurrences: int = 1,
    sources: Optional[List[str]] = None
) -> NewsClassification:
    """Clasifica una noticia o posteo utilizando Gemini con failover automático ante 503."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return rule_based_fallback_classifier(title, content)

    configured_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    models_to_try = [configured_model] + [m for m in FALLBACK_MODELS if m != configured_model]

    trimmed_content = content[:6000] if content else "(Sin contenido adicional)"
    sources_str = ", ".join(sources) if sources else (source or "Desconocido")

    for model_name in models_to_try:
        try:
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=0.1,
                max_retries=0,
                timeout=20
            )
            structured_llm = llm.with_structured_output(NewsClassification)
            chain = CLASSIFIER_PROMPT | structured_llm

            result: NewsClassification = chain.invoke({
                "title": title or "Sin título",
                "source": source or "Desconocido",
                "occurrences": occurrences,
                "sources_str": sources_str,
                "content": trimmed_content
            })
            return result

        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "high demand" in err_msg.lower() or "UNAVAILABLE" in err_msg:
                logger.warning(f"Modelo '{model_name}' con alta demanda temporal (503). Probando modelo alternativo...")
                continue
            elif "404" in err_msg or "not found" in err_msg.lower():
                logger.warning(f"Modelo '{model_name}' no disponible (404). Conmutando al siguiente modelo...")
                continue
            else:
                logger.warning(f"Error con modelo '{model_name}' ({err_msg}). Intentando alternativa...")
                continue

    logger.warning("No se pudo clasificar mediante la API de Gemini tras intentar con todos los modelos. Utilizando clasificador de respaldo.")
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

def classifier_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Clasificador."""
    search_results = {r["url"]: r for r in state.get("search_results", [])}
    crawled_articles = state.get("crawled_articles", [])
    analyzed_list: List[Dict[str, Any]] = []

    logger.info(f"Agente Clasificador evaluando {len(crawled_articles)} noticias y publicaciones...")

    for article in crawled_articles:
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
            sources=sources
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
            classification=classification
        )
        analyzed_list.append(analyzed_item.model_dump())

    return {
        "classified_news": analyzed_list
    }

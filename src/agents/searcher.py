"""Agente Buscador: Búsqueda multi-medio en Argentina (Clarín, La Nación, Infobae, etc.) con deduplicación inteligente."""

import re
import difflib
import logging
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Set
from src.state import GraphState, SearchResult

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

try:
    from nltk.stem.snowball import SnowballStemmer
    stemmer = SnowballStemmer("spanish")
except Exception:
    stemmer = None

logger = logging.getLogger(__name__)

# Lista de principales medios argentinos a priorizar
ARGENTINE_DOMAINS = [
    "clarin.com",
    "lanacion.com.ar",
    "infobae.com",
    "pagina12.com.ar",
    "tn.com.ar",
    "perfil.com",
    "ambito.com",
    "cronista.com",
    "eldestapeweb.com",
    "c5n.com",
    "tiempoar.com.ar"
]

def clean_title(title: str) -> str:
    """Limpia el título eliminando firmas de medios y normalizando caracteres."""
    if not title:
        return ""
    # Quitar marcas de medios al final (ej: "- Clarín", "| Infobae", "- TN", etc.)
    title = re.sub(
        r"(\s*[-–|•]\s*(Clarin|Clarín|La Nación|La Nacion|Infobae|TN|Perfil|Página 12|Pagina 12|Ámbito|Ambito|El Cronista|C5N|El Destape|Olé|Ole|Minuto Uno|Tiempo Argentino).*)$",
        "",
        title,
        flags=re.IGNORECASE
    )
    # Normalizar acentos y pasar a minúsculas
    norm = unicodedata.normalize("NFKD", title).encode("ASCII", "ignore").decode("utf-8").lower()
    # Eliminar signos de puntuación
    norm = re.sub(r"[^a-z0-9\s]", " ", norm)
    return " ".join(norm.split())

def extract_significant_stems(title_clean: str) -> Set[str]:
    """Extrae las raíces léxicas de palabras significativas para comparar noticias."""
    stopwords = {
        "para", "como", "sobre", "este", "esta", "estos", "estas", "entre", "desde",
        "hasta", "hacia", "segun", "contra", "donde", "cuando", "quien", "porque",
        "universidad", "buenos", "aires", "tras", "cada", "pero", "ante", "noticia"
    }
    words = [w for w in title_clean.split() if len(w) >= 4 and w not in stopwords]
    if stemmer:
        return {stemmer.stem(w) for w in words}
    return {w[:5] for w in words}

def is_duplicate_story(t1: str, t2: str) -> bool:
    """Determina si dos titulares refieren al mismo hecho noticioso."""
    c1, c2 = clean_title(t1), clean_title(t2)
    if not c1 or not c2:
        return False
    if c1 == c2:
        return True

    # Comparación directa de secuencias
    ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    if ratio >= 0.58:
        return True

    # Comparación de raíces léxicas y palabras clave compartidas
    stems1 = extract_significant_stems(c1)
    stems2 = extract_significant_stems(c2)
    if not stems1 or not stems2:
        return False

    intersection = stems1 & stems2
    min_len = min(len(stems1), len(stems2))

    # Si comparten al menos 3 conceptos clave y representan más del 40% del titular menor
    if len(intersection) >= 3 and (len(intersection) / min_len) >= 0.40:
        return True

    return False

def clean_url(url: str) -> str:
    """Normaliza la URL quitando parámetros de tracking innecesarios."""
    try:
        parsed = urllib.parse.urlparse(url)
        # Quitar parámetros de analítica como utm_*, fbclid, oc, ref
        query_params = urllib.parse.parse_qsl(parsed.query)
        filtered_params = [
            (k, v) for k, v in query_params 
            if not k.startswith("utm_") and k not in ("fbclid", "oc", "ref", "ref_src")
        ]
        clean_query = urllib.parse.urlencode(filtered_params)
        return urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, ""
        )).rstrip("/")
    except Exception:
        return url

def deduplicate_results(results: List[SearchResult]) -> List[SearchResult]:
    """Elimina noticias duplicadas tanto por URL canónica como por similitud de contenido/titular."""
    unique_results: List[SearchResult] = []
    seen_urls: Set[str] = set()

    for item in results:
        norm_url = clean_url(item.url)
        if norm_url in seen_urls:
            continue

        # Verificar si ya existe una noticia sobre el mismo hecho en los resultados únicos
        duplicate_found = False
        for existing in unique_results:
            if is_duplicate_story(item.title, existing.title):
                logger.info(f"Noticia duplicada descartada: '{item.title}' (similar a '{existing.title}')")
                duplicate_found = True
                break

        if not duplicate_found:
            seen_urls.add(norm_url)
            unique_results.append(item)

    return unique_results

def fetch_rss_query(rss_query: str, max_items: int = 15) -> List[SearchResult]:
    """Consulta Google News RSS para Argentina con una consulta dada."""
    items_out: List[SearchResult] = []
    encoded = urllib.parse.quote_plus(rss_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=es-419&gl=AR&ceid=AR:es-419"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
            items = root.findall(".//item")
            for item in items[:max_items]:
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else None
                source = item.find("source").text if item.find("source") is not None else "Medio Argentino"
                desc = item.find("description").text if item.find("description") is not None else ""

                if link and title:
                    items_out.append(SearchResult(
                        title=title.strip(),
                        url=link.strip(),
                        snippet=desc.strip(),
                        date=pub_date,
                        source=source
                    ))
    except Exception as e:
        logger.warning(f"Error consultando RSS '{rss_query}': {e}")

    return items_out

def search_uba_news(query: str, max_results: int = 5) -> List[SearchResult]:
    """Búsqueda integral en los principales medios argentinos (Clarín, La Nación, Infobae, TN, etc.) sin duplicados."""
    raw_results: List[SearchResult] = []

    search_term = query.strip()
    if "universidad" not in search_term.lower() and "buenos aires" not in search_term.lower():
        search_term = f"{search_term} Universidad de Buenos Aires"

    logger.info(f"Buscando en todos los medios argentinos para '{search_term}'...")

    # 1. Búsqueda específica en los principales medios nacionales (Clarín, La Nación, Infobae, Página 12, TN, Perfil, Ámbito)
    media_sites = " OR ".join([f"site:{d}" for d in ARGENTINE_DOMAINS[:7]])
    targeted_query = f"({query} OR \"Universidad de Buenos Aires\") ({media_sites})"
    targeted_items = fetch_rss_query(targeted_query, max_items=max_results * 3)
    raw_results.extend(targeted_items)

    # 2. Búsqueda general en DuckDuckGo News Argentina
    try:
        with DDGS() as ddgs:
            try:
                ddg_items = list(ddgs.news(
                    query=f"{search_term}",
                    region="ar-es",
                    safesearch="off",
                    timelimit="m",
                    max_results=max_results * 2
                ))
            except TypeError:
                ddg_items = list(ddgs.news(
                    keywords=f"{search_term}",
                    region="ar-es",
                    safesearch="off",
                    timelimit="m",
                    max_results=max_results * 2
                ))

            for d in ddg_items:
                url = d.get("url") or d.get("href")
                if url:
                    raw_results.append(SearchResult(
                        title=d.get("title", "").strip(),
                        url=url.strip(),
                        snippet=d.get("body", "").strip(),
                        date=d.get("date"),
                        source=d.get("source", "DuckDuckGo News")
                    ))
    except Exception as e:
        logger.warning(f"DuckDuckGo temporalmente no disponible ({e}), confiando en RSS nacional.")

    # 3. Búsqueda abierta general en Google News Argentina como complemento
    broad_items = fetch_rss_query(f"{search_term}", max_items=max_results * 2)
    raw_results.extend(broad_items)

    # 4. Deduplicación inteligente (URLs y Titulares similares)
    deduped = deduplicate_results(raw_results)

    logger.info(f"Recolectados {len(raw_results)} artículos totales -> {len(deduped)} únicos tras deduplicación.")
    return deduped[:max_results]

def searcher_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Buscador."""
    query = state.get("query", "UBA")
    max_results = state.get("max_results", 5)

    search_results = search_uba_news(query=query, max_results=max_results)

    logger.info(f"Agente Buscador finalizó con {len(search_results)} artículos únicos.")
    return {
        "search_results": [r.model_dump() for r in search_results]
    }

"""Agente Buscador: Búsqueda multi-medio en Argentina y conectores MCP de Redes Sociales (X, Instagram, Facebook, TikTok, YouTube, Reddit) con fechas normalizadas, conteo de apariciones y agrupación de fuentes."""

import re
import difflib
import logging
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Set, Optional, Tuple
from src.state import GraphState, SearchResult, MentionItem
from src.mcp_social import (
    extract_and_format_date,
    search_all_social_mcp,
)

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

# Dominios de redes sociales soportados
SOCIAL_DOMAINS = {
    "x.com": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "reddit.com": "Reddit",
    "linkedin.com": "LinkedIn",
    "threads.net": "Threads",
}

SOURCE_NORMALIZATION = {
    "clarin.com": "Clarín",
    "clarin": "Clarín",
    "clarín": "Clarín",
    "lanacion.com.ar": "La Nación",
    "la nacion": "La Nación",
    "la nación": "La Nación",
    "infobae.com": "Infobae",
    "infobae": "Infobae",
    "pagina12.com.ar": "Página 12",
    "pagina 12": "Página 12",
    "página 12": "Página 12",
    "página|12": "Página 12",
    "tn.com.ar": "TN",
    "tn": "TN",
    "todo noticias": "TN",
    "perfil.com": "Perfil",
    "perfil": "Perfil",
    "ambito.com": "Ámbito",
    "ambito": "Ámbito",
    "ámbito": "Ámbito",
    "cronista.com": "El Cronista",
    "el cronista": "El Cronista",
    "eldestapeweb.com": "El Destape",
    "el destape": "El Destape",
    "c5n.com": "C5N",
    "c5n": "C5N",
    "tiempoar.com.ar": "Tiempo Argentino",
    "tiempo argentino": "Tiempo Argentino",
    "a24.com": "A24",
    "minutouno": "Minuto Uno",
    "minutouno.com": "Minuto Uno",
}


def detect_social_platform(url: str, raw_source: Optional[str] = None, title: str = "") -> Tuple[bool, Optional[str]]:
    """Detecta si una URL o fuente pertenece a una red social y devuelve su etiqueta enriquecida."""
    url_lower = (url or "").lower()
    src_lower = (raw_source or "").lower()

    # Si raw_source ya viene formateado desde mcp_social (ej: "X (@PCArgentino)", "Instagram (@uba_ar)", "Facebook (ubaint)")
    if raw_source and any(raw_source.startswith(p) for p in ("X (", "Instagram", "Facebook", "TikTok", "YouTube", "Reddit")):
        return True, raw_source

    for domain, platform_name in SOCIAL_DOMAINS.items():
        if domain in url_lower or platform_name.lower() in src_lower:
            if domain in ("x.com", "twitter.com"):
                m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", url)
                if m and m.group(1).lower() not in ("search", "hashtag", "i", "home", "explore"):
                    return True, f"X (@{m.group(1)})"
                return True, "X (Twitter)"
            if domain == "instagram.com":
                m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
                if m and m.group(1).lower() not in ("p", "reel", "reels", "explore", "stories", "tv"):
                    return True, f"Instagram (@{m.group(1)})"
                return True, "Instagram (@uba_ar)" if "uba" in (title or "").lower() else "Instagram"
            if domain in ("facebook.com", "fb.com"):
                m = re.search(r"facebook\.com/([A-Za-z0-9_.]+)", url)
                if m and m.group(1).lower() not in ("groups", "watch", "share", "events", "pages"):
                    return True, f"Facebook ({m.group(1)})"
                return True, "Facebook"
            if domain == "tiktok.com":
                m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url)
                if m:
                    return True, f"TikTok (@{m.group(1)})"
                return True, "TikTok"
            if domain == "reddit.com":
                m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
                if m:
                    return True, f"Reddit (r/{m.group(1)})"
                return True, "Reddit"
            if domain in ("youtube.com", "youtu.be"):
                return True, raw_source if (raw_source and "youtube" in src_lower) else "YouTube"
            return True, platform_name

    return False, None


def normalize_source_name(title: str, url: str, raw_source: Optional[str]) -> Tuple[str, str]:
    """Normaliza el nombre de la fuente y devuelve (source_name, source_type)."""
    is_social, social_label = detect_social_platform(url, raw_source, title)
    if is_social and social_label:
        return social_label, "red_social"

    extracted_from_title = None
    if title:
        m = re.search(r"\s+[-–|•]\s+([^-–|•]{2,35})$", title.strip())
        if m:
            extracted_from_title = m.group(1).strip()

    candidate = raw_source
    if not candidate or candidate in ("Medio Argentino", "DuckDuckGo News", "Medio Web"):
        candidate = extracted_from_title or candidate

    if candidate:
        norm_key = candidate.strip().lower()
        if norm_key in SOURCE_NORMALIZATION:
            return SOURCE_NORMALIZATION[norm_key], "medio"
        return candidate.strip(), "medio"

    try:
        netloc = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        if netloc in SOURCE_NORMALIZATION:
            return SOURCE_NORMALIZATION[netloc], "medio"
        if netloc and "google.com" not in netloc:
            return netloc, "medio"
    except Exception:
        pass

    return "Medio Argentino", "medio"


def strip_source_suffix(title: str) -> str:
    """Elimina la firma del medio al final del titular manteniendo el texto legible."""
    if not title:
        return ""
    cleaned = re.sub(
        r"(\s*[-–|•]\s*(Clarin\.com|Clarin|Clarín|La Nación|La Nacion|Infobae|TN|Perfil|Página\s*\|?\s*12|Pagina\s*12|Ámbito|Ambito|El Cronista|C5N|El Destape|Olé|Ole|Minuto\s*Uno|Minutouno|Tiempo Argentino|iProfesional|A24\.com|A24|Crónica|Radio Mitre|Diario Popular).*)$",
        "",
        title.strip(),
        flags=re.IGNORECASE
    )
    return cleaned.strip() or title.strip()


def clean_title(title: str) -> str:
    """Limpia el título eliminando firmas de medios y normalizando caracteres para comparación."""
    if not title:
        return ""
    title = strip_source_suffix(title)
    norm = unicodedata.normalize("NFKD", title).encode("ASCII", "ignore").decode("utf-8").lower()
    norm = re.sub(r"[^a-z0-9\s]", " ", norm)
    return " ".join(norm.split())


def extract_significant_stems(title_clean: str) -> Set[str]:
    """Extrae las raíces léxicas de palabras significativas para comparar noticias."""
    stopwords = {
        "para", "como", "sobre", "este", "esta", "estos", "estas", "entre", "desde",
        "hasta", "hacia", "segun", "contra", "donde", "cuando", "quien", "porque",
        "universidad", "buenos", "aires", "tras", "cada", "pero", "ante", "noticia",
        "video", "post", "instagram", "twitter", "facebook", "tiktok", "youtube"
    }
    words = [w for w in title_clean.split() if len(w) >= 4 and w not in stopwords]
    if stemmer:
        return {stemmer.stem(w) for w in words}
    return {w[:5] for w in words}


def strip_boilerplate_terms(text_clean: str) -> str:
    """Elimina términos genéricos de la institución para comparar únicamente el hecho noticioso."""
    stripped = re.sub(
        r"\b(universidad\s+de\s+buenos\s+aires|universidad|buenos\s+aires|argentina|uba|reddit|instagram|facebook|twitter|tiktok|youtube)\b",
        " ",
        text_clean
    )
    return " ".join(stripped.split())


def is_duplicate_story(t1: str, t2: str) -> bool:
    """Determina si dos titulares o publicaciones refieren al mismo hecho noticioso."""
    c1 = strip_boilerplate_terms(clean_title(t1))
    c2 = strip_boilerplate_terms(clean_title(t2))
    if not c1 or not c2:
        return False

    stems1 = extract_significant_stems(c1)
    stems2 = extract_significant_stems(c2)
    if len(stems1) < 2 or len(stems2) < 2:
        return False

    if c1 == c2:
        return True

    ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    if ratio >= 0.58:
        return True

    intersection = stems1 & stems2
    min_len = min(len(stems1), len(stems2))

    if len(intersection) >= 3 and (len(intersection) / min_len) >= 0.40:
        return True

    return False


def clean_url(url: str) -> str:
    """Normaliza la URL quitando parámetros de tracking innecesarios."""
    try:
        parsed = urllib.parse.urlparse(url)
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


def clean_social_title(title: str, snippet: str) -> str:
    """Limpia títulos ruidosos de redes sociales y usa el snippet del post cuando el título es solo el nombre del perfil."""
    t = (title or "").strip()
    s = (snippet or "").strip()
    if t.lower().count("facebook") >= 2 and s:
        s_clean = re.sub(r"^[A-Za-z0-9\s,]+\s*·\s*", "", s).strip()
        first_part = t.split("- Facebook")[0].strip()
        return f"{first_part}: {s_clean[:110]}"

    if len(extract_significant_stems(clean_title(t))) < 2 and s:
        s_clean = re.sub(r"^[A-Za-z0-9\s,]+\s*·\s*", "", s).strip()
        if len(extract_significant_stems(clean_title(s_clean))) >= 2:
            return f"{t} — {s_clean[:110]}" if t else s_clean[:120]

    return t


def aggregate_and_count_results(results: List[SearchResult]) -> List[SearchResult]:
    """
    Agrupa noticias similares en lugar de descartarlas:
    - Cuenta la cantidad de veces que apareció cada noticia (`occurrences`).
    - Consolida la lista de todas las fuentes (`sources`).
    - Normaliza y asigna fechas (`date`) a cada noticia y a cada aparición (`mentions`).
    """
    grouped_results: List[SearchResult] = []
    seen_exact_articles: Set[Tuple[str, str]] = set()
    seen_urls: Set[str] = set()

    for item in results:
        if not item.title or not item.url:
            continue

        norm_source, norm_type = normalize_source_name(item.title, item.url, item.source)
        item.source = norm_source
        item.source_type = norm_type  # type: ignore
        if norm_type == "red_social":
            item.title = clean_social_title(item.title, item.snippet)

        # Garantizar fecha formateada DD/MM/YYYY
        formatted_date = extract_and_format_date(item.date, item.url, item.snippet)
        item.date = formatted_date

        clean_t = clean_title(item.title)
        if len(extract_significant_stems(clean_t)) < 2 and norm_type == "medio":
            continue

        norm_url = clean_url(item.url)
        exact_key = (clean_t, norm_source.lower())
        if norm_url in seen_urls or exact_key in seen_exact_articles:
            continue

        seen_urls.add(norm_url)
        seen_exact_articles.add(exact_key)

        mention = MentionItem(
            title=strip_source_suffix(item.title),
            url=item.url,
            source=norm_source,
            source_type=norm_type,  # type: ignore
            date=formatted_date
        )

        # Las publicaciones de redes sociales (X, Instagram, Facebook) solo se agrupan entre sí si tratan del mismo post,
        # o se mantienen visibles para mostrar las publicaciones de cada red social.
        matched_group: Optional[SearchResult] = None
        for existing in grouped_results:
            if existing.source_type == norm_type and is_duplicate_story(item.title, existing.title):
                matched_group = existing
                break

        if matched_group is not None:
            matched_group.occurrences += 1
            if norm_source not in matched_group.sources:
                matched_group.sources.append(norm_source)
            matched_group.mentions.append(mention)

            if not matched_group.snippet and item.snippet:
                matched_group.snippet = item.snippet
            if not matched_group.date and formatted_date:
                matched_group.date = formatted_date

            logger.info(
                f"Noticia repetida contabilizada (aparición #{matched_group.occurrences}): "
                f"'{strip_source_suffix(item.title)}' [{norm_source} - {formatted_date}] -> agrupada en "
                f"'{matched_group.title}' | Fuentes ({len(matched_group.sources)}): {', '.join(matched_group.sources)}"
            )
        else:
            item.title = strip_source_suffix(item.title)
            item.occurrences = 1
            item.sources = [norm_source]
            item.mentions = [mention]
            grouped_results.append(item)

    return grouped_results


def fetch_rss_query(rss_query: str, max_items: int = 20) -> List[SearchResult]:
    """Consulta Google News RSS para Argentina con una consulta dada y extrae fecha normalizada."""
    items_out: List[SearchResult] = []
    encoded = urllib.parse.quote_plus(rss_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=es-419&gl=AR&ceid=AR:es-419"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
                desc_clean = re.sub(r"<[^>]+>", " ", desc).strip()

                if link and title:
                    norm_src, src_type = normalize_source_name(title, link, source)
                    formatted_date = extract_and_format_date(pub_date, link, desc_clean)
                    items_out.append(SearchResult(
                        title=title.strip(),
                        url=link.strip(),
                        snippet=desc_clean,
                        date=formatted_date,
                        source=norm_src,
                        source_type=src_type  # type: ignore
                    ))
    except Exception as e:
        logger.warning(f"Error consultando RSS '{rss_query}': {e}")

    return items_out


def search_uba_news(query: str, max_results: int = 5) -> List[SearchResult]:
    """
    Búsqueda integral en los principales medios argentinos y conectores MCP de Redes Sociales (X, Instagram, Facebook).
    - Agrupa y cuenta la cantidad de apariciones de noticias repetidas junto con sus fuentes.
    - Incluye resultados de X (Twitter), Instagram y Facebook con sus fechas exactas.
    """
    raw_media_results: List[SearchResult] = []

    search_term = query.strip()
    if "universidad" not in search_term.lower() and "buenos aires" not in search_term.lower():
        search_term = f"{search_term} Universidad de Buenos Aires"

    logger.info(f"Buscando en medios argentinos y MCP de Redes Sociales (X, Instagram, Facebook) para '{search_term}'...")

    # 1. Búsqueda amplia en los principales medios nacionales para capturar todas las apariciones repetidas
    media_sites = " OR ".join([f"site:{d}" for d in ARGENTINE_DOMAINS])
    targeted_query = f"({query} OR \"Universidad de Buenos Aires\") ({media_sites})"
    raw_media_results.extend(fetch_rss_query(targeted_query, max_items=max(25, max_results * 5)))

    # 2. Búsqueda general en Google News Argentina
    raw_media_results.extend(fetch_rss_query(f"{search_term}", max_items=max(25, max_results * 5)))

    # 3. Búsqueda en DuckDuckGo News Argentina
    try:
        with DDGS(timeout=6) as ddgs:
            ddg_items = list(ddgs.news(
                f"{search_term}",
                region="ar-es",
                safesearch="off",
                timelimit="m",
                max_results=max(10, max_results * 2)
            ))
            for d in ddg_items:
                url = d.get("url") or d.get("href")
                title = d.get("title", "")
                if url and title:
                    norm_src, src_type = normalize_source_name(title, url, d.get("source"))
                    raw_media_results.append(SearchResult(
                        title=title.strip(),
                        url=url.strip(),
                        snippet=d.get("body", "").strip(),
                        date=extract_and_format_date(d.get("date"), url, d.get("body", "")),
                        source=norm_src,
                        source_type=src_type  # type: ignore
                    ))
    except Exception as e:
        logger.warning(f"DuckDuckGo News temporalmente no disponible ({e}), usando RSS.")

    # 4. Búsqueda mediante Conectores MCP de Redes Sociales: X (Twitter), Instagram y Facebook
    social_by_platform = search_all_social_mcp(query=query, max_per_platform=3)
    x_items = aggregate_and_count_results(social_by_platform.get("x", []))
    ig_items = aggregate_and_count_results(social_by_platform.get("instagram", []))
    fb_items = aggregate_and_count_results(social_by_platform.get("facebook", []))

    # 5. Agrupación y conteo de noticias en medios tradicionales
    media_groups = aggregate_and_count_results(raw_media_results)
    media_groups.sort(key=lambda r: r.occurrences, reverse=True)

    # 6. Selección equilibrada garantizando presencia de X, Instagram y Facebook + las noticias más repetidas
    guaranteed_social: List[SearchResult] = []
    if x_items:
        guaranteed_social.append(x_items[0])
    if ig_items:
        guaranteed_social.append(ig_items[0])
    if fb_items:
        guaranteed_social.append(fb_items[0])

    extra_social = x_items[1:] + ig_items[1:] + fb_items[1:]

    if max_results <= 3:
        selected = (media_groups[:1] + guaranteed_social)[:max_results]
    else:
        # Reservar espacio para X, Instagram y Facebook (3 items) y el resto para las noticias con más apariciones
        social_slots = min(len(guaranteed_social), max(3, round(max_results * 0.45)))
        media_slots = max(1, max_results - social_slots)
        selected_media = media_groups[:media_slots]
        selected_social = (guaranteed_social + extra_social)[: max_results - len(selected_media)]
        selected = selected_media + selected_social

        if len(selected) < max_results:
            remaining = media_groups[len(selected_media):] + extra_social
            selected.extend(remaining[: max_results - len(selected)])

    # Ordenar primero por cantidad de apariciones (las más repetidas arriba)
    selected.sort(key=lambda r: r.occurrences, reverse=True)

    total_mentions = sum(r.occurrences for r in selected)
    logger.info(
        f"Seleccionados {len(selected)} resultados ({total_mentions} apariciones totales) "
        f"incluyendo Medios + MCP Redes Sociales (X: {len(x_items)}, Instagram: {len(ig_items)}, Facebook: {len(fb_items)})."
    )
    return selected


def searcher_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Buscador."""
    query = state.get("query", "UBA")
    max_results = state.get("max_results", 6)

    search_results = search_uba_news(query=query, max_results=max_results)

    logger.info(f"Agente Buscador finalizó con {len(search_results)} noticias y publicaciones sociales.")
    return {
        "search_results": [r.model_dump() for r in search_results]
    }

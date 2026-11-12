"""Agente Crawler: Utiliza Crawl4AI y Playwright para extraer el contenido limpio de cada noticia o publicación en redes sociales."""

import asyncio
import ipaddress
import logging
import socket
import urllib.parse
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from src.state import GraphState, CrawledArticle

logger = logging.getLogger(__name__)

SOCIAL_DOMAINS_FAST = ("x.com", "twitter.com", "instagram.com", "tiktok.com", "facebook.com", "reddit.com", "linkedin.com")
MAX_CONCURRENT_CRAWLS = 3


def _is_safe_public_url(url: str) -> bool:
    """Valida esquema HTTP/HTTPS y bloquea objetivos loopback o redes privadas (mitigación SSRF)."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    host = parsed.hostname.lower().strip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal")):
        return False

    def _is_blocked_ip(ip_obj: ipaddress._BaseAddress) -> bool:
        return (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        )

    try:
        return not _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass

    try:
        for info in socket.getaddrinfo(host, None):
            ip_str = info[4][0].split("%")[0]
            if _is_blocked_ip(ipaddress.ip_address(ip_str)):
                return False
    except OSError:
        # Si DNS falla aquí, Playwright reportará el error al intentar navegar
        return True
    return True


async def _crawl_single_url(
    crawler: AsyncWebCrawler,
    url: str,
    semaphore: asyncio.Semaphore,
) -> CrawledArticle:
    """Extrae una URL respetando el límite de concurrencia y validación SSRF."""
    if not _is_safe_public_url(url):
        logger.warning(f"URL bloqueada por política de seguridad SSRF: {url}")
        return CrawledArticle(
            url=url,
            success=False,
            error_message="URL rechazada: solo se permiten destinos públicos HTTP/HTTPS.",
        )

    is_social = any(d in url.lower() for d in SOCIAL_DOMAINS_FAST)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10 if is_social else 20,
        exclude_external_links=True,
        remove_overlay_elements=True,
        delay_before_return_html=1.5 if is_social else 2.2,
        page_timeout=15000 if is_social else 30000,
    )

    async with semaphore:
        logger.info(f"Crawleando {'red social' if is_social else 'artículo'}: {url}")
        try:
            result = await crawler.arun(url=url, config=run_config)
            if result.success:
                content = result.markdown.raw_markdown if hasattr(result.markdown, "raw_markdown") else str(result.markdown)
                logger.info(f"Scraping exitoso ({len(content)} caracteres): {url}")
                return CrawledArticle(
                    url=url,
                    title=result.metadata.get("title", "") if result.metadata else "",
                    content_markdown=content[:12000],
                    success=True,
                )
            logger.warning(f"Aviso al scrapear {url} (se usará snippet/metadatos): {result.error_message}")
            return CrawledArticle(
                url=url,
                success=False,
                error_message=result.error_message or "Extracción limitada, usando metadatos de búsqueda",
            )
        except Exception as e:
            logger.warning(f"Excepción al crawlear {url} (se usará snippet/metadatos): {e}")
            return CrawledArticle(url=url, success=False, error_message=str(e))


async def crawl_articles_async(urls: List[str]) -> List[CrawledArticle]:
    """Descarga y extrae el contenido de una lista de URLs en paralelo acotado usando Crawl4AI."""
    if not urls:
        return []

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        browser_type="chromium",
    )
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CRAWLS)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        tasks = [_crawl_single_url(crawler, url, semaphore) for url in urls]
        return list(await asyncio.gather(*tasks))

async def crawler_node_async(state: GraphState) -> Dict[str, Any]:
    """Nodo asíncrono para el Agente Crawler en LangGraph."""
    search_results = state.get("search_results", [])
    urls = [r["url"] for r in search_results if r.get("url")]

    logger.info(f"Agente Crawler procesando {len(urls)} páginas web y redes sociales con Playwright...")
    crawled = await crawl_articles_async(urls)

    return {
        "crawled_articles": [c.model_dump() for c in crawled]
    }

def crawler_node(state: GraphState) -> Dict[str, Any]:
    """Wrapper síncrono para LangGraph ejecutando el crawler asíncrono."""
    try:
        return asyncio.run(crawler_node_async(state))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(crawler_node_async(state))
        finally:
            loop.close()

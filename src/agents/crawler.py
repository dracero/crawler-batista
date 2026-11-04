"""Agente Crawler: Utiliza Crawl4AI y Playwright para extraer el contenido limpio de cada noticia."""

import asyncio
import logging
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from src.state import GraphState, CrawledArticle

logger = logging.getLogger(__name__)

async def crawl_articles_async(urls: List[str]) -> List[CrawledArticle]:
    """Descarga y extrae el contenido de una lista de URLs usando Crawl4AI."""
    crawled_results: List[CrawledArticle] = []
    if not urls:
        return crawled_results

    # Configuración de navegador de Crawl4AI (Playwright)
    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        browser_type="chromium"
    )

    # Configuración de extracción limpia
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=20,  # Evita bloques pequeños irrelevantes
        exclude_external_links=True,
        remove_overlay_elements=True,
        delay_before_return_html=2.5,  # Permite que redirecciones y JS completen la navegación
        page_timeout=35000  # 35 segundos de timeout por página
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for url in urls:
            logger.info(f"Crawleando artículo: {url}")
            try:
                result = await crawler.arun(url=url, config=run_config)
                if result.success:
                    # Obtenemos el texto en markdown filtrado
                    content = result.markdown.raw_markdown if hasattr(result.markdown, 'raw_markdown') else str(result.markdown)
                    crawled_results.append(CrawledArticle(
                        url=url,
                        title=result.metadata.get("title", "") if result.metadata else "",
                        content_markdown=content[:12000],  # Truncado seguro para tokens del LLM
                        success=True
                    ))
                    logger.info(f"Scraping exitoso ({len(content)} caracteres): {url}")
                else:
                    logger.warning(f"Fallo al scrapear {url}: {result.error_message}")
                    crawled_results.append(CrawledArticle(
                        url=url,
                        success=False,
                        error_message=result.error_message or "Extracción fallida"
                    ))
            except Exception as e:
                logger.error(f"Error inesperado al crawlear {url}: {e}")
                crawled_results.append(CrawledArticle(
                    url=url,
                    success=False,
                    error_message=str(e)
                ))

    return crawled_results

async def crawler_node_async(state: GraphState) -> Dict[str, Any]:
    """Nodo asíncrono para el Agente Crawler en LangGraph."""
    search_results = state.get("search_results", [])
    urls = [r["url"] for r in search_results if r.get("url")]

    logger.info(f"Agente Crawler procesando {len(urls)} páginas web con Playwright...")
    crawled = await crawl_articles_async(urls)

    return {
        "crawled_articles": [c.model_dump() for c in crawled]
    }

def crawler_node(state: GraphState) -> Dict[str, Any]:
    """Wrapper síncrono para LangGraph ejecutando el crawler asíncrono."""
    try:
        return asyncio.run(crawler_node_async(state))
    except RuntimeError:
        # En caso de que ya exista un bucle activo
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(crawler_node_async(state))
        finally:
            loop.close()

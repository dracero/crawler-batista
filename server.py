"""Servidor FastAPI Full-Stack para el Monitor de Noticias y Redes Sociales UBA."""

import os
import json
import asyncio
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from src.graph import build_graph
from src.state import GraphState

app = FastAPI(
    title="Monitor UBA Full-Stack",
    description="Aplicación web para monitorear y clasificar noticias y redes sociales de la UBA usando LangGraph, Crawl4AI y Gemini",
    version="1.1.0"
)

# CORS para desarrollo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    query: str = Field(default="UBA", description="Tema o términos de búsqueda")
    max_results: int = Field(default=6, ge=1, le=15, description="Cantidad de noticias/temas a analizar")

@app.get("/api/config")
async def get_config():
    """Devuelve el estado de la configuración de LLM y de los conectores MCP de Redes Sociales."""
    from src.mcp_social import get_mcp_social_status
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    return {
        "gemini_configured": bool(gemini_key),
        "model": model,
        "default_query": "UBA",
        "mcp_social": get_mcp_social_status()
    }

@app.post("/api/analyze")
async def analyze_news(request: AnalyzeRequest):
    """Ejecuta el grafo multi-agente y retorna los resultados analizados."""
    try:
        graph = build_graph()
        initial_state: GraphState = {
            "query": request.query,
            "max_results": request.max_results,
            "search_results": [],
            "crawled_articles": [],
            "classified_news": [],
            "summary_report": "",
            "errors": []
        }

        final_state = await asyncio.to_thread(graph.invoke, initial_state)

        news = final_state.get("classified_news", [])
        total = len(news)
        total_mentions = sum(n.get("occurrences", 1) for n in news)
        social_count = sum(1 for n in news if n.get("source_type") == "red_social")
        media_count = total - social_count
        buenas = sum(1 for n in news if n["classification"]["sentiment"] == "BUENA")
        malas = sum(1 for n in news if n["classification"]["sentiment"] == "MALA")
        neutras = sum(1 for n in news if n["classification"]["sentiment"] == "NEUTRAL")
        avg_score = (sum(n["classification"]["score"] for n in news) / total) if total > 0 else 0.0

        return {
            "query": request.query,
            "total": total,
            "stats": {
                "buenas": buenas,
                "malas": malas,
                "neutras": neutras,
                "total_mentions": total_mentions,
                "social_count": social_count,
                "media_count": media_count,
                "pct_buenas": round((buenas / total * 100), 1) if total > 0 else 0,
                "pct_malas": round((malas / total * 100), 1) if total > 0 else 0,
                "pct_neutras": round((neutras / total * 100), 1) if total > 0 else 0,
                "avg_score": round(avg_score, 2)
            },
            "news": news,
            "summary_report": final_state.get("summary_report", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def event_generator(query: str, max_results: int) -> AsyncGenerator[str, None]:
    """Generador SSE para transmitir los estados de avance de cada agente."""
    def send_event(step: str, message: str, payload: dict = None):
        data = {"step": step, "message": message, "payload": payload or {}}
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield send_event("start", "Iniciando flujo multi-agente...")
        await asyncio.sleep(0.2)

        # Paso 1: Búsqueda en Medios y Redes Sociales + Conteo de Apariciones
        yield send_event("searcher", f"Agente Buscador: Rastreando medios y redes sociales sobre '{query}' y contabilizando apariciones...")
        from src.agents.searcher import search_uba_news
        search_results = await asyncio.to_thread(search_uba_news, query=query, max_results=max_results)
        total_found_mentions = sum(r.occurrences for r in search_results)
        yield send_event(
            "searcher_done",
            f"Búsqueda finalizada: {len(search_results)} temas ({total_found_mentions} apariciones en medios y redes).",
            {
                "count": len(search_results),
                "total_mentions": total_found_mentions,
                "titles": [r.title for r in search_results]
            }
        )
        await asyncio.sleep(0.3)

        # Paso 2: Crawler
        yield send_event("crawler", "Agente Crawler: Extrayendo contenido web y redes sociales con Crawl4AI + Playwright...")
        from src.agents.crawler import crawl_articles_async
        urls = [r.url for r in search_results if r.url]
        crawled_articles = await crawl_articles_async(urls)
        success_count = sum(1 for c in crawled_articles if c.success)
        yield send_event("crawler_done", f"Crawling completado: {success_count}/{len(urls)} páginas extraídas.")
        await asyncio.sleep(0.3)

        # Paso 3: Clasificador
        yield send_event("classifier", "Agente Clasificador: Evaluando impacto y sentimiento con Google Gemini...")
        from src.agents.classifier import classify_article, build_combined_content
        analyzed_list = []
        search_meta = {r.url: r for r in search_results}

        for idx, article in enumerate(crawled_articles, 1):
            url = article.url
            meta = search_meta.get(url)
            title = (meta.title if meta and meta.title else article.title) or "Noticia UBA"
            source = meta.source if meta else "Medio Web"
            source_type = meta.source_type if meta else "medio"
            date = meta.date if meta else None
            occurrences = meta.occurrences if meta else 1
            sources = (meta.sources if meta and meta.sources else [source])
            mentions = [m.model_dump() for m in meta.mentions] if meta and meta.mentions else []

            content = build_combined_content(meta.model_dump() if meta else {}, article.content_markdown)

            classification = await asyncio.to_thread(
                classify_article,
                title=title,
                content=content,
                source=source,
                occurrences=occurrences,
                sources=sources
            )
            analyzed_item = {
                "url": url,
                "title": title,
                "source": source,
                "source_type": source_type,
                "date": date,
                "occurrences": occurrences,
                "sources": sources,
                "mentions": mentions,
                "classification": classification.model_dump()
            }
            analyzed_list.append(analyzed_item)
            yield send_event("classifier_progress", f"Clasificado {idx}/{len(crawled_articles)}: {classification.sentiment} ({occurrences}x)", {
                "current": idx,
                "total": len(crawled_articles),
                "last_item": analyzed_item
            })

        # Paso 4: Resumen y reportero
        from src.agents.reporter import generate_text_report
        report_md = generate_text_report(analyzed_list, query)

        total = len(analyzed_list)
        total_mentions = sum(n.get("occurrences", 1) for n in analyzed_list)
        social_count = sum(1 for n in analyzed_list if n.get("source_type") == "red_social")
        media_count = total - social_count
        buenas = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "BUENA")
        malas = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "MALA")
        neutras = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "NEUTRAL")
        avg_score = (sum(n["classification"]["score"] for n in analyzed_list) / total) if total > 0 else 0.0

        stats = {
            "buenas": buenas,
            "malas": malas,
            "neutras": neutras,
            "total_mentions": total_mentions,
            "social_count": social_count,
            "media_count": media_count,
            "pct_buenas": round((buenas / total * 100), 1) if total > 0 else 0,
            "pct_malas": round((malas / total * 100), 1) if total > 0 else 0,
            "pct_neutras": round((neutras / total * 100), 1) if total > 0 else 0,
            "avg_score": round(avg_score, 2)
        }

        yield send_event("completed", "¡Análisis completado exitosamente!", {
            "query": query,
            "total": total,
            "stats": stats,
            "news": analyzed_list,
            "summary_report": report_md
        })

    except Exception as e:
        yield send_event("error", f"Error en la ejecución: {str(e)}")

@app.get("/api/stream")
async def stream_analysis(query: str = "UBA", max_results: int = 6):
    """Endpoint SSE para transmitir en tiempo real cada paso del análisis."""
    return StreamingResponse(
        event_generator(query=query, max_results=max_results),
        media_type="text/event-stream"
    )

# Servir carpeta estática (apunta a la build de Astro en frontend/dist o a static)
astro_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
static_dir = astro_dist if os.path.exists(astro_dist) else os.path.join(os.path.dirname(__file__), "static")

if os.path.exists(os.path.join(static_dir, "_astro")):
    app.mount("/_astro", StaticFiles(directory=os.path.join(static_dir, "_astro")), name="astro_assets")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Sirve la interfaz web compilada con Astro.js."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Compila el frontend con: cd frontend && npm run build</h1>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

"""Servidor FastAPI para el Monitor de Noticias y Redes Sociales UBA."""

import os
import json
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from src.graph import build_async_graph
from src.state import GraphState, AnalyzeResponse, ConfigResponse
from src.agents.reporter import compute_stats
from src.key_manager import get_key_rotator
from src.mcp_social import get_mcp_social_status

app = FastAPI(
    title="Monitor UBA",
    description="Aplicación web para monitorear y clasificar noticias y redes sociales sobre la UBA usando LangGraph, Crawl4AI y Gemini",
    version="1.2.0",
)

_default_origins = [
    "http://localhost:4321",
    "http://127.0.0.1:4321",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AnalyzeRequest(BaseModel):
    query: str = Field(default="UBA", min_length=1, max_length=200, description="Filtro temático dentro del ámbito UBA")
    max_results: int = Field(default=6, ge=1, le=15, description="Cantidad máxima de temas agrupados a analizar")


def _build_initial_state(query: str, max_results: int) -> GraphState:
    return {
        "query": query,
        "max_results": max_results,
        "search_results": [],
        "crawled_articles": [],
        "classified_news": [],
        "summary_report": "",
    }


@app.get("/api/config", response_model=ConfigResponse)
async def get_config():
    """Devuelve el estado de la configuración de LLM y las plataformas sociales activas."""
    key_status = get_key_rotator().get_status()
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    return {
        "gemini_configured": key_status["total_keys"] > 0,
        "gemini_keys_count": key_status["total_keys"],
        "gemini_key_status": key_status,
        "model": model,
        "default_query": "UBA",
        "social_status": get_mcp_social_status(),
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_news(request: AnalyzeRequest):
    """Ejecuta el grafo multi-agente de LangGraph y retorna los resultados analizados."""
    try:
        async_graph = build_async_graph()
        initial_state = _build_initial_state(request.query, request.max_results)
        final_state = await async_graph.ainvoke(initial_state)

        news = final_state.get("classified_news", [])
        return {
            "query": request.query,
            "total": len(news),
            "stats": compute_stats(news),
            "news": news,
            "summary_report": final_state.get("summary_report", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def event_generator(query: str, max_results: int) -> AsyncGenerator[str, None]:
    """Generador SSE que transmite eventos de avance directamente desde el grafo LangGraph."""
    def send_event(step: str, message: str, payload: dict | None = None):
        data = {"step": step, "message": message, "payload": payload or {}}
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield send_event("start", "Iniciando flujo multi-agente LangGraph...")
        yield send_event("searcher", f"Agente Buscador: Rastreando medios y redes sociales sobre '{query}'...")

        async_graph = build_async_graph()
        initial_state = _build_initial_state(query, max_results)
        classified_news = []
        summary_report = ""

        async for update in async_graph.astream(initial_state, stream_mode="updates"):
            if "searcher" in update:
                search_results = update["searcher"].get("search_results", [])
                total_found_mentions = sum(r.get("occurrences", 1) for r in search_results)
                yield send_event(
                    "searcher_done",
                    f"Búsqueda finalizada: {len(search_results)} temas ({total_found_mentions} apariciones en medios y redes).",
                    {
                        "count": len(search_results),
                        "total_mentions": total_found_mentions,
                        "titles": [r.get("title", "") for r in search_results],
                    },
                )
                yield send_event("crawler", "Agente Crawler: Extrayendo contenido web y redes sociales con Crawl4AI + Playwright...")

            elif "crawler" in update:
                crawled_articles = update["crawler"].get("crawled_articles", [])
                success_count = sum(1 for c in crawled_articles if c.get("success"))
                yield send_event("crawler_done", f"Crawling completado: {success_count}/{len(crawled_articles)} páginas extraídas.")
                yield send_event("classifier", "Agente Clasificador: Evaluando impacto y sentimiento con Google Gemini...")

            elif "classifier" in update:
                classified_news = update["classifier"].get("classified_news", [])
                total_cls = len(classified_news)
                if total_cls > 0:
                    last_item = classified_news[-1]
                    sentiment = last_item.get("classification", {}).get("sentiment", "NEUTRAL")
                    occ = last_item.get("occurrences", 1)
                    yield send_event(
                        "classifier_progress",
                        f"Clasificados {total_cls}/{total_cls}: último {sentiment} ({occ}x)",
                        {"current": total_cls, "total": total_cls, "last_item": last_item},
                    )

            elif "reporter" in update:
                summary_report = update["reporter"].get("summary_report", "")

        stats = compute_stats(classified_news)
        yield send_event(
            "completed",
            "Análisis completado.",
            {
                "query": query,
                "total": len(classified_news),
                "stats": stats,
                "news": classified_news,
                "summary_report": summary_report,
            },
        )

    except Exception as e:
        yield send_event("error", f"Error en la ejecución: {str(e)}")


@app.get("/api/stream")
async def stream_analysis(
    query: str = Query(default="UBA", min_length=1, max_length=200, description="Filtro temático dentro del ámbito UBA"),
    max_results: int = Query(default=6, ge=1, le=15, description="Cantidad máxima de temas agrupados a analizar"),
):
    """Endpoint SSE para transmitir en tiempo real cada paso del grafo LangGraph."""
    return StreamingResponse(
        event_generator(query=query.strip() or "UBA", max_results=max_results),
        media_type="text/event-stream",
    )


# Servir compilado estático de Astro (frontend/dist)
static_dir = os.path.join(os.path.dirname(__file__), "frontend", "dist")
astro_assets = os.path.join(static_dir, "_astro")
if os.path.exists(astro_assets):
    app.mount("/_astro", StaticFiles(directory=astro_assets), name="astro_assets")


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

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("UVICORN_RELOAD", "").lower() == "true"
    uvicorn.run("server:app", host=host, port=port, reload=reload_enabled)

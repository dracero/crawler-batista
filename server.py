"""Servidor FastAPI Full-Stack para el Monitor de Noticias UBA."""

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
    description="Aplicación web para monitorear y clasificar noticias de la UBA usando LangGraph, Crawl4AI y Gemini",
    version="1.0.0"
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
    max_results: int = Field(default=5, ge=1, le=15, description="Cantidad de noticias a analizar")

@app.get("/api/config")
async def get_config():
    """Devuelve el estado de la configuración de LLM."""
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    return {
        "gemini_configured": bool(gemini_key),
        "model": model,
        "default_query": "UBA"
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

        # Ejecutar en threadpool para no bloquear el bucle de eventos
        final_state = await asyncio.to_thread(graph.invoke, initial_state)

        # Calcular métricas resumen
        news = final_state.get("classified_news", [])
        total = len(news)
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

        # Paso 1: Búsqueda
        yield send_event("searcher", f"Agente Buscador: Localizando noticias recientes sobre '{query}'...")
        from src.agents.searcher import search_uba_news
        search_results = await asyncio.to_thread(search_uba_news, query=query, max_results=max_results)
        yield send_event("searcher_done", f"Búsqueda finalizada: {len(search_results)} artículos encontrados.", {
            "count": len(search_results),
            "titles": [r.title for r in search_results]
        })
        await asyncio.sleep(0.3)

        # Paso 2: Crawler
        yield send_event("crawler", f"Agente Crawler: Extrayendo contenido web con Crawl4AI y Playwright...")
        from src.agents.crawler import crawl_articles_async
        urls = [r.url for r in search_results if r.url]
        crawled_articles = await crawl_articles_async(urls)
        success_count = sum(1 for c in crawled_articles if c.success)
        yield send_event("crawler_done", f"Crawling completado: {success_count}/{len(urls)} páginas extraídas con éxito.")
        await asyncio.sleep(0.3)

        # Paso 3: Clasificador
        yield send_event("classifier", "Agente Clasificador: Evaluando impacto y sentimiento con Google Gemini...")
        from src.agents.classifier import classify_article
        analyzed_list = []
        search_meta = {r.url: r for r in search_results}

        for idx, article in enumerate(crawled_articles, 1):
            url = article.url
            meta = search_meta.get(url)
            title = article.title or (meta.title if meta else "Noticia UBA")
            content = article.content_markdown or (meta.snippet if meta else "")
            source = meta.source if meta else "Medio Web"
            date = meta.date if meta else None

            classification = await asyncio.to_thread(
                classify_article, title=title, content=content, source=source
            )
            analyzed_item = {
                "url": url,
                "title": title,
                "source": source,
                "date": date,
                "classification": classification.model_dump()
            }
            analyzed_list.append(analyzed_item)
            yield send_event("classifier_progress", f"Clasificado {idx}/{len(crawled_articles)}: {classification.sentiment}", {
                "current": idx,
                "total": len(crawled_articles),
                "last_item": analyzed_item
            })

        # Paso 4: Resumen y reportero
        from src.agents.reporter import generate_text_report
        report_md = generate_text_report(analyzed_list, query)

        total = len(analyzed_list)
        buenas = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "BUENA")
        malas = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "MALA")
        neutras = sum(1 for n in analyzed_list if n["classification"]["sentiment"] == "NEUTRAL")
        avg_score = (sum(n["classification"]["score"] for n in analyzed_list) / total) if total > 0 else 0.0

        stats = {
            "buenas": buenas,
            "malas": malas,
            "neutras": neutras,
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
async def stream_analysis(query: str = "UBA", max_results: int = 5):
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

"""Agente Reportero: Consolida los resultados, calcula estadísticas y genera el informe final."""

import logging
from typing import Dict, Any, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from src.state import GraphState

logger = logging.getLogger(__name__)

def generate_text_report(classified_news: List[Dict[str, Any]], query: str) -> str:
    """Genera un informe detallado en formato Markdown."""
    total = len(classified_news)
    if total == 0:
        return f"# Reporte de Noticias UBA\nNo se encontraron noticias para la consulta: '{query}'."

    buenas = [n for n in classified_news if n["classification"]["sentiment"] == "BUENA"]
    malas = [n for n in classified_news if n["classification"]["sentiment"] == "MALA"]
    neutras = [n for n in classified_news if n["classification"]["sentiment"] == "NEUTRAL"]

    pct_buenas = (len(buenas) / total) * 100
    pct_malas = (len(malas) / total) * 100
    pct_neutras = (len(neutras) / total) * 100

    avg_score = sum(n["classification"]["score"] for n in classified_news) / total

    lines = [
        f"# 📊 Monitoreo de Noticias UBA",
        f"**Búsqueda:** {query} | **Total analizadas:** {total}\n",
        f"## Resumen Ejecutivo",
        f"- 🟢 **Noticias Buenas (Positivas):** {len(buenas)} ({pct_buenas:.1f}%)",
        f"- 🔴 **Noticias Malas (Negativas):** {len(malas)} ({pct_malas:.1f}%)",
        f"- ⚪ **Noticias Neutrales:** {len(neutras)} ({pct_neutras:.1f}%)",
        f"- 📈 **Índice de Sentimiento Promedio:** `{avg_score:+.2f}` (de -1.0 a +1.0)\n",
        f"---",
        f"## Detalle de Artículos Analizados\n"
    ]

    for idx, item in enumerate(classified_news, start=1):
        c = item["classification"]
        badge = "🟢 BUENA" if c["sentiment"] == "BUENA" else ("🔴 MALA" if c["sentiment"] == "MALA" else "⚪ NEUTRAL")
        
        lines.append(f"### {idx}. {item.get('title', 'Sin título')}")
        lines.append(f"- **Veredicto:** {badge} (Score: `{c['score']:+.2f}`) | **Categoría:** {c['category']}")
        if item.get("source"):
            lines.append(f"- **Fuente:** {item['source']}")
        lines.append(f"- **Enlace:** [{item.get('url')}]({item.get('url')})")
        lines.append(f"- **Justificación:** {c['justification']}")
        if c.get("key_points"):
            lines.append("- **Puntos destacados:**")
            for kp in c["key_points"]:
                lines.append(f"  - {kp}")
        lines.append("")

    return "\n".join(lines)

def print_rich_dashboard(classified_news: List[Dict[str, Any]], query: str):
    """Muestra una vista interactiva y visual en la terminal con Rich."""
    console = Console()
    total = len(classified_news)

    if total == 0:
        console.print(Panel(f"[yellow]No se encontraron noticias para: {query}[/yellow]", title="Sin resultados"))
        return

    buenas = sum(1 for n in classified_news if n["classification"]["sentiment"] == "BUENA")
    malas = sum(1 for n in classified_news if n["classification"]["sentiment"] == "MALA")
    neutras = sum(1 for n in classified_news if n["classification"]["sentiment"] == "NEUTRAL")

    header_text = Text()
    header_text.append(f"Noticias analizadas: {total} | ", style="bold cyan")
    header_text.append(f"Buenas: {buenas} ({(buenas/total)*100:.0f}%) ", style="bold green")
    header_text.append(f"| Malas: {malas} ({(malas/total)*100:.0f}%) ", style="bold red")
    header_text.append(f"| Neutras: {neutras} ({(neutras/total)*100:.0f}%)", style="bold white")

    console.print(Panel(header_text, title="🎓 Monitor Inteligente UBA - LangGraph + Crawl4AI + Gemini", expand=False))

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Sentimiento", width=12, justify="center")
    table.add_column("Score", width=8, justify="right")
    table.add_column("Categoría", width=22)
    table.add_column("Título y Resumen", justify="left")

    for item in classified_news:
        c = item["classification"]
        sent = c["sentiment"]
        score_val = c["score"]

        if sent == "BUENA":
            sentiment_styled = "[bold green]🟢 BUENA[/bold green]"
            score_styled = f"[green]{score_val:+.2f}[/green]"
        elif sent == "MALA":
            sentiment_styled = "[bold red]🔴 MALA[/bold red]"
            score_styled = f"[red]{score_val:+.2f}[/red]"
        else:
            sentiment_styled = "[bold white]⚪ NEUTRA[/bold white]"
            score_styled = f"[white]{score_val:+.2f}[/white]"

        details = f"[bold]{item.get('title', 'Sin título')}[/bold]\n[dim]{c.get('justification', '')}[/dim]\n[blue underline]{item.get('url')}[/blue underline]"
        table.add_row(sentiment_styled, score_styled, c.get("category", "General"), details)

    console.print(table)

def reporter_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Reportero."""
    classified = state.get("classified_news", [])
    query = state.get("query", "UBA")

    report_markdown = generate_text_report(classified, query)

    # Imprimir resumen estilizado en consola si estamos ejecutando en vivo
    try:
        print_rich_dashboard(classified, query)
    except Exception as e:
        logger.warning(f"No se pudo renderizar dashboard Rich: {e}")

    return {
        "summary_report": report_markdown
    }


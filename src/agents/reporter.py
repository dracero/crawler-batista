"""Agente Reportero: Consolida los resultados, contabiliza apariciones, fechas y fuentes (medios + MCP redes sociales) y genera el informe final."""

import logging
from typing import Dict, Any, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from src.state import GraphState

logger = logging.getLogger(__name__)

def compute_stats(classified_news: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calcula estadísticas agregadas y porcentajes de sentimiento para un lote analizado."""
    total = len(classified_news)
    total_mentions = sum(n.get("occurrences", 1) for n in classified_news)
    social_count = sum(1 for n in classified_news if n.get("source_type") == "red_social")
    media_count = total - social_count

    buenas = sum(1 for n in classified_news if n["classification"]["sentiment"] == "BUENA")
    malas = sum(1 for n in classified_news if n["classification"]["sentiment"] == "MALA")
    neutras = sum(1 for n in classified_news if n["classification"]["sentiment"] == "NEUTRAL")
    avg_score = (sum(n["classification"]["score"] for n in classified_news) / total) if total > 0 else 0.0

    return {
        "buenas": buenas,
        "malas": malas,
        "neutras": neutras,
        "total_mentions": total_mentions,
        "social_count": social_count,
        "media_count": media_count,
        "pct_buenas": round((buenas / total * 100), 1) if total > 0 else 0.0,
        "pct_malas": round((malas / total * 100), 1) if total > 0 else 0.0,
        "pct_neutras": round((neutras / total * 100), 1) if total > 0 else 0.0,
        "avg_score": round(avg_score, 2),
    }


def generate_text_report(classified_news: List[Dict[str, Any]], query: str) -> str:
    """Genera un informe detallado en formato Markdown con fechas, conteo de apariciones y fuentes."""
    total = len(classified_news)
    if total == 0:
        return f"# Reporte de Noticias UBA\nNo se encontraron noticias para la consulta: '{query}'."

    stats = compute_stats(classified_news)
    lines = [
        f"# 📊 Monitoreo de Noticias y Redes Sociales UBA",
        f"**Búsqueda:** {query} | **Temas/Noticias analizadas:** {total} | **Total de apariciones detectadas:** {stats['total_mentions']}\n",
        f"## Resumen Ejecutivo",
        f"- 🟢 **Noticias Buenas (Positivas):** {stats['buenas']} ({stats['pct_buenas']:.1f}%)",
        f"- 🔴 **Noticias Malas (Negativas):** {stats['malas']} ({stats['pct_malas']:.1f}%)",
        f"- ⚪ **Noticias Neutrales:** {stats['neutras']} ({stats['pct_neutras']:.1f}%)",
        f"- 🔁 **Apariciones Totales Contabilizadas:** {stats['total_mentions']} menciones en medios y redes",
        f"- 📰 **Medios Periodísticos:** {stats['media_count']} | 📱 **Redes Sociales (X, Instagram, Facebook):** {stats['social_count']}",
        f"- 📈 **Índice de Sentimiento Promedio:** `{stats['avg_score']:+.2f}` (de -1.0 a +1.0)\n",
        f"---",
        f"## Detalle de Noticias y Publicaciones Analizadas\n"
    ]

    for idx, item in enumerate(classified_news, start=1):
        c = item["classification"]
        badge = "🟢 BUENA" if c["sentiment"] == "BUENA" else ("🔴 MALA" if c["sentiment"] == "MALA" else "⚪ NEUTRAL")
        occ = item.get("occurrences", 1)
        date_str = item.get("date") or "Sin fecha"
        sources_list = item.get("sources") or ([item.get("source")] if item.get("source") else ["Desconocido"])
        src_type_badge = "📱 Red Social" if item.get("source_type") == "red_social" else "📰 Medio"

        lines.append(f"### {idx}. {item.get('title', 'Sin título')}")
        lines.append(f"- **Fecha:** 📅 `{date_str}` | **Origen:** {src_type_badge}")
        lines.append(f"- **Veredicto:** {badge} (Score: `{c['score']:+.2f}`) | **Categoría:** {c['category']}")
        lines.append(f"- **Cantidad de apariciones:** 🔁 **{occ}** {'vez' if occ == 1 else 'veces'}")
        lines.append(f"- **Fuentes ({len(sources_list)}):** {', '.join(sources_list)}")
        lines.append(f"- **Enlace principal:** [{item.get('url')}]({item.get('url')})")
        lines.append(f"- **Justificación:** {c['justification']}")
        if c.get("key_points"):
            lines.append("- **Puntos destacados:**")
            for kp in c["key_points"]:
                lines.append(f"  - {kp}")
        mentions = item.get("mentions") or []
        if len(mentions) > 1:
            lines.append(f"- **Detalle de las {len(mentions)} apariciones registradas:**")
            for m in mentions:
                m_src = m.get("source", "Fuente")
                m_date = m.get("date") or date_str
                m_title = m.get("title", "")
                m_url = m.get("url", "")
                lines.append(f"  - **[{m_date}] {m_src}**: [{m_title}]({m_url})")
        lines.append("")

    return "\n".join(lines)

def print_rich_dashboard(classified_news: List[Dict[str, Any]], query: str):
    """Muestra una vista interactiva y visual en la terminal con Rich incluyendo fecha, apariciones y fuentes."""
    console = Console()
    total = len(classified_news)

    if total == 0:
        console.print(Panel(f"[yellow]No se encontraron noticias para: {query}[/yellow]", title="Sin resultados"))
        return

    stats = compute_stats(classified_news)
    total_mentions = stats["total_mentions"]
    social_count = stats["social_count"]
    buenas = stats["buenas"]
    malas = stats["malas"]
    neutras = stats["neutras"]

    header_text = Text()
    header_text.append(f"Noticias: {total} (🔁 {total_mentions} apariciones) | ", style="bold cyan")
    header_text.append(f"📱 Redes (X/IG/FB): {social_count} | ", style="bold magenta")
    header_text.append(f"Buenas: {buenas} ({stats['pct_buenas']:.0f}%) ", style="bold green")
    header_text.append(f"| Malas: {malas} ({stats['pct_malas']:.0f}%) ", style="bold red")
    header_text.append(f"| Neutras: {neutras} ({stats['pct_neutras']:.0f}%)", style="bold white")

    console.print(Panel(header_text, title="🎓 Monitor UBA - Medios y Redes Sociales", expand=False))

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Sentimiento", width=12, justify="center")
    table.add_column("Score", width=7, justify="right")
    table.add_column("Fecha", width=16, justify="center")
    table.add_column("Apariciones", width=11, justify="center")
    table.add_column("Fuentes", width=24)
    table.add_column("Título y Resumen", justify="left")

    for item in classified_news:
        c = item["classification"]
        sent = c["sentiment"]
        score_val = c["score"]
        occ = item.get("occurrences", 1)
        date_str = item.get("date") or "Sin fecha"
        sources_list = item.get("sources") or ([item.get("source")] if item.get("source") else ["Web"])
        is_social = item.get("source_type") == "red_social"

        if sent == "BUENA":
            sentiment_styled = "[bold green]🟢 BUENA[/bold green]"
            score_styled = f"[green]{score_val:+.2f}[/green]"
        elif sent == "MALA":
            sentiment_styled = "[bold red]🔴 MALA[/bold red]"
            score_styled = f"[red]{score_val:+.2f}[/red]"
        else:
            sentiment_styled = "[bold white]⚪ NEUTRAL[/bold white]"
            score_styled = f"[white]{score_val:+.2f}[/white]"

        occ_styled = f"[bold yellow]🔁 {occ}x[/bold yellow]" if occ > 1 else f"[dim]1x[/dim]"
        prefix_icon = "📱 " if is_social else "📰 "
        sources_styled = f"{prefix_icon}" + ", ".join(sources_list)
        date_styled = f"[cyan]📅 {date_str}[/cyan]"

        details = (
            f"[bold]{item.get('title', 'Sin título')}[/bold] [cyan]({c.get('category', 'General')})[/cyan]\n"
            f"[dim]{c.get('justification', '')}[/dim]\n"
            f"[blue underline]{item.get('url')}[/blue underline]"
        )
        table.add_row(sentiment_styled, score_styled, date_styled, occ_styled, sources_styled, details)

    console.print(table)

def reporter_node(state: GraphState) -> Dict[str, Any]:
    """Nodo del Grafo para el Agente Reportero."""
    classified = state.get("classified_news", [])
    query = state.get("query", "UBA")

    report_markdown = generate_text_report(classified, query)

    try:
        print_rich_dashboard(classified, query)
    except Exception as e:
        logger.warning(f"No se pudo renderizar dashboard Rich: {e}")

    return {
        "summary_report": report_markdown
    }

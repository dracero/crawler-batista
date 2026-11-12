"""Punto de entrada principal para el Monitor y Clasificador de Noticias UBA con LangGraph, Crawl4AI y Gemini."""

import os
import sys
import argparse
import json
import logging
from dotenv import load_dotenv
from rich.console import Console

# Cargar variables de entorno desde .env si existe
load_dotenv()

from src.graph import build_graph

# Configurar logging básico
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
# Reducir verbosidad de librerías externas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("duckduckgo_search").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)

def main():
    parser = argparse.ArgumentParser(
        description="Monitor de Noticias y Redes Sociales UBA (LangGraph + Crawl4AI + Gemini)"
    )
    parser.add_argument(
        "--query",
        type=str,
        default="UBA",
        help="Tema o consulta a buscar (por defecto: 'UBA')"
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Número máximo de artículos a crawlear y analizar (por defecto: 5)"
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default=None,
        help="Ruta de archivo para guardar el informe en Markdown (ej: reporte.md)"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Ruta de archivo para exportar los datos analizados en JSON"
    )

    args = parser.parse_args()
    console = Console()

    console.rule("[bold cyan]🎓 Monitor de Noticias UBA[/bold cyan]")
    console.print(f"[bold]Consulta:[/bold] {args.query}")
    console.print(f"[bold]Límite de artículos:[/bold] {args.max_results}")
    
    from src.key_manager import get_gemini_keys
    gemini_keys = get_gemini_keys()
    if not gemini_keys:
        console.print(
            "[yellow]⚠️  Aviso: GEMINI_API_KEY no detectada en .env o variables de entorno.\n"
            "   Se utilizará el clasificador heurístico de respaldo.\n"
            "   Para usar Google Gemini, añade tu(s) clave(s) en el archivo .env (ver .env.example).[/yellow]\n"
        )
    else:
        model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        keys_note = f" ({len(gemini_keys)} API keys en rotación)" if len(gemini_keys) > 1 else ""
        console.print(f"[green]✓ Google Gemini configurado con modelo: {model}{keys_note}[/green]\n")

    app = build_graph()

    initial_state = {
        "query": args.query,
        "max_results": args.max_results,
        "search_results": [],
        "crawled_articles": [],
        "classified_news": [],
        "summary_report": "",
    }

    try:
        with console.status("[bold green]Ejecutando grafo multi-agente...[/bold green]", spinner="dots"):
            final_state = app.invoke(initial_state)

        # Exportar a Markdown si se solicitó
        if args.output_md and final_state.get("summary_report"):
            with open(args.output_md, "w", encoding="utf-8") as f:
                f.write(final_state["summary_report"])
            console.print(f"\n[green]✓ Informe Markdown guardado en:[/green] {args.output_md}")

        # Exportar a JSON si se solicitó
        if args.output_json and final_state.get("classified_news"):
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(final_state["classified_news"], f, ensure_ascii=False, indent=2)
            console.print(f"[green]✓ Datos en JSON guardados en:[/green] {args.output_json}")

    except KeyboardInterrupt:
        console.print("\n[red]Operación cancelada por el usuario.[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]Error durante la ejecución del grafo:[/bold red] {e}")
        logging.exception(e)
        sys.exit(1)

if __name__ == "__main__":
    main()


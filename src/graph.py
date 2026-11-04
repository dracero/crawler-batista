"""Orquestación del Grafo Multi-Agente con LangGraph."""

import logging
from langgraph.graph import StateGraph, START, END
from src.state import GraphState
from src.agents.searcher import searcher_node
from src.agents.crawler import crawler_node
from src.agents.classifier import classifier_node
from src.agents.reporter import reporter_node

logger = logging.getLogger(__name__)

def build_graph():
    """Construye y compila el flujo de agentes con LangGraph."""
    workflow = StateGraph(GraphState)

    # 1. Definición de nodos (Agentes)
    workflow.add_node("searcher", searcher_node)
    workflow.add_node("crawler", crawler_node)
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("reporter", reporter_node)

    # 2. Definición del flujo secuencial
    workflow.add_edge(START, "searcher")
    workflow.add_edge("searcher", "crawler")
    workflow.add_edge("crawler", "classifier")
    workflow.add_edge("classifier", "reporter")
    workflow.add_edge("reporter", END)

    app = workflow.compile()
    return app


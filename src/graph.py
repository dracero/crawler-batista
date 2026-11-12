"""Orquestación del Grafo Multi-Agente con LangGraph."""

import asyncio
import logging
from langgraph.graph import StateGraph, START, END
from src.state import GraphState
from src.agents.searcher import searcher_node
from src.agents.crawler import crawler_node, crawler_node_async
from src.agents.classifier import classifier_node
from src.agents.reporter import reporter_node

logger = logging.getLogger(__name__)


async def _searcher_async(state: GraphState):
    return await asyncio.to_thread(searcher_node, state)


async def _classifier_async(state: GraphState):
    return await asyncio.to_thread(classifier_node, state)


async def _reporter_async(state: GraphState):
    return await asyncio.to_thread(reporter_node, state)


def build_graph():
    """Construye y compila el flujo síncrono de agentes con LangGraph (CLI)."""
    workflow = StateGraph(GraphState)
    workflow.add_node("searcher", searcher_node)
    workflow.add_node("crawler", crawler_node)
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("reporter", reporter_node)

    workflow.add_edge(START, "searcher")
    workflow.add_edge("searcher", "crawler")
    workflow.add_edge("crawler", "classifier")
    workflow.add_edge("classifier", "reporter")
    workflow.add_edge("reporter", END)
    return workflow.compile()


def build_async_graph():
    """Construye y compila el flujo asíncrono de agentes con LangGraph (FastAPI y SSE)."""
    workflow = StateGraph(GraphState)
    workflow.add_node("searcher", _searcher_async)
    workflow.add_node("crawler", crawler_node_async)
    workflow.add_node("classifier", _classifier_async)
    workflow.add_node("reporter", _reporter_async)

    workflow.add_edge(START, "searcher")
    workflow.add_edge("searcher", "crawler")
    workflow.add_edge("crawler", "classifier")
    workflow.add_edge("classifier", "reporter")
    workflow.add_edge("reporter", END)
    return workflow.compile()


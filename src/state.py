from typing import List, Optional, Literal, TypedDict
from pydantic import BaseModel, Field

class SearchResult(BaseModel):
    title: str = Field(description="Título del resultado de búsqueda")
    url: str = Field(description="URL de la noticia")
    snippet: str = Field(default="", description="Fragmento o bajada de la noticia")
    date: Optional[str] = Field(default=None, description="Fecha de publicación si está disponible")
    source: Optional[str] = Field(default=None, description="Medio o fuente de noticias")

class CrawledArticle(BaseModel):
    url: str
    title: str = ""
    content_markdown: str = ""
    success: bool = True
    error_message: Optional[str] = None

class NewsClassification(BaseModel):
    sentiment: Literal["BUENA", "MALA", "NEUTRAL"] = Field(
        description="Clasificación del impacto/tono de la noticia para la UBA: BUENA (positiva), MALA (negativa), NEUTRAL (informativa)"
    )
    score: float = Field(
        description="Puntuación numérica de sentimiento entre -1.0 (muy mala/negativa) y +1.0 (muy buena/positiva)"
    )
    category: str = Field(
        description="Categoría principal (ej: Presupuesto y Finanzas, Logros Académicos y Científicos, Gremial y Paros, Infraestructura, Vida Universitaria)"
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="Puntos clave destacados de la noticia"
    )
    justification: str = Field(
        description="Justificación detallada de por qué se considera buena, mala o neutral para la UBA"
    )

class AnalyzedNews(BaseModel):
    url: str
    title: str
    source: Optional[str] = None
    date: Optional[str] = None
    classification: NewsClassification

class GraphState(TypedDict):
    query: str
    max_results: int
    search_results: List[dict]
    crawled_articles: List[dict]
    classified_news: List[dict]
    summary_report: str
    errors: List[str]


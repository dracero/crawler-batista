from typing import List, Optional, Literal, TypedDict, Dict, Any
from pydantic import BaseModel, Field

class MentionItem(BaseModel):
    title: str = Field(description="Título en esta fuente")
    url: str = Field(description="URL de la mención o artículo")
    source: str = Field(description="Nombre del medio o red social")
    source_type: Literal["medio", "red_social"] = Field(default="medio", description="Tipo de fuente")
    date: Optional[str] = Field(default=None, description="Fecha de publicación si está disponible")

class SearchResult(BaseModel):
    title: str = Field(description="Título del resultado de búsqueda")
    url: str = Field(description="URL de la noticia o publicación")
    snippet: str = Field(default="", description="Fragmento o bajada de la noticia o posteo")
    date: Optional[str] = Field(default=None, description="Fecha de publicación si está disponible")
    source: Optional[str] = Field(default=None, description="Medio o red social principal")
    source_type: Literal["medio", "red_social"] = Field(default="medio", description="Indica si proviene de un medio tradicional o red social")
    occurrences: int = Field(default=1, description="Cantidad de veces que apareció esta noticia/tema")
    sources: List[str] = Field(default_factory=list, description="Lista de todas las fuentes/medios/redes donde apareció")
    mentions: List[MentionItem] = Field(default_factory=list, description="Detalle de todas las apariciones agrupadas")

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
        description="Categoría principal (ej: Presupuesto y Finanzas, Logros Académicos y Científicos, Gremial y Paros, Infraestructura, Vida Universitaria, Redes Sociales y Opinión)"
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
    source_type: Literal["medio", "red_social"] = "medio"
    date: Optional[str] = None
    occurrences: int = 1
    sources: List[str] = Field(default_factory=list)
    mentions: List[MentionItem] = Field(default_factory=list)
    classification: NewsClassification

class AnalysisStats(BaseModel):
    buenas: int = 0
    malas: int = 0
    neutras: int = 0
    total_mentions: int = 0
    social_count: int = 0
    media_count: int = 0
    pct_buenas: float = 0.0
    pct_malas: float = 0.0
    pct_neutras: float = 0.0
    avg_score: float = 0.0


class AnalyzeResponse(BaseModel):
    query: str
    total: int
    stats: AnalysisStats
    news: List[AnalyzedNews]
    summary_report: str


class ConfigResponse(BaseModel):
    gemini_configured: bool
    gemini_keys_count: int
    gemini_key_status: Dict[str, Any]
    model: str
    default_query: str = "UBA"
    social_status: Dict[str, Any]


class GraphState(TypedDict):
    query: str
    max_results: int
    search_results: List[dict]
    crawled_articles: List[dict]
    classified_news: List[dict]
    summary_report: str

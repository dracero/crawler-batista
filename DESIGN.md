# Decisiones de Arquitectura y Diseño (`DESIGN.md`)

Este documento registra la motivación, los límites de alcance, las alternativas evaluadas y los riesgos conocidos de **Monitor UBA**. Para instalación y operación diaria, consulta [README.md](README.md).

---

## 1. Declaración del Problema

El seguimiento manual de la cobertura pública sobre la Universidad de Buenos Aires (UBA) dispersa artículos sindicados entre múltiples portales periodísticos y publicaciones en redes sociales. Sin consolidación previa, una misma noticia aparece duplicada como entradas independientes, las marcas temporales llegan en formatos incompatibles (RFC-2822, ISO-8601, texto relativo o IDs de plataforma) y las cuotas gratuitas de modelos LLM se agotan ante picos de consulta.

El sistema automatiza la recolección, agrupación por tema, extracción de texto completo y clasificación de impacto institucional en una ejecución bajo demanda.

---

## 2. Objetivos y No-Objetivos

### Objetivos
- Consultar medios periodísticos argentinos y publicaciones públicas en redes sociales (`X`, `Instagram`, `Facebook`) filtradas al ámbito UBA.
- Agrupar coberturas equivalentes para preservar la cantidad de apariciones (`occurrences`), la lista de fuentes y el historial de menciones individuales (`mentions`).
- Normalizar fechas reales en zona horaria Argentina (`America/Argentina/Buenos_Aires`), manteniendo `None` (`Sin fecha`) cuando no exista una fecha verificable.
- Ejecutar una única tubería canónica en LangGraph tanto desde la CLI como desde los endpoints REST y SSE de FastAPI.
- Operar de forma segura como herramienta de monitoreo local sin depender de CDNs externos ni exponer claves en el frontend.

### No-Objetivos
- **Daemon continuo o base de datos histórica**: cada análisis se ejecuta bajo demanda sin persistencia ni planificación periódica.
- **Servicio multi-tenant público en Internet**: la aplicación carece de autenticación de usuarios y gestión de cuentas; no está pensada para exposición directa sin proxy inverso, autenticación y control de tasa externo.
- **Buscador de propósito general**: las consultas y el prompt del clasificador están acotados al contexto institucional de la UBA en Argentina.
- **Integración con APIs pagas de redes sociales**: no utiliza credenciales OAuth ni planes comerciales de X, Meta o TikTok.

---

## 3. Alternativas Consideradas

| Decisión | Opción Elegida | Alternativa Descartada | Compromiso (Trade-off) |
| :--- | :--- | :--- | :--- |
| Orquestación del flujo | `LangGraph` (`StateGraph` síncrono y asíncrono compartidos) | Funciones `asyncio` imperativas separadas por interfaz | Introduce dependencia de `langgraph` para un grafo lineal, pero garantiza un único flujo compartido entre CLI, `/api/analyze` y `/api/stream`. |
| Descubrimiento en redes sociales | Consultas `DuckDuckGo` (`site:...`) + enriquecimiento opcional con `api.fxtwitter.com` | APIs oficiales de X/Meta o servidor MCP JSON-RPC independiente | Evita costos de API y sobrecarga de transporte IPC para adaptadores internos; depende de la indexación pública de buscadores y disponibilidad de FxTwitter. |
| Extracción de contenido web | `Crawl4AI` + `Playwright` (Chromium headless con semáforo) | `requests` + parser HTML estático | Mayor consumo de RAM y tiempo de arranque frente a HTML estático, a cambio de renderizar páginas con carga dinámica y generar Markdown limpio. |
| Agrupación de noticias repetidas | Stemming en español (`SnowballStemmer`) + similitud de secuencia | Embeddings vectoriales por cada titular | Precisión limitada a equivalencia léxica frente a similitud semántica profunda, pero sin latencia ni consumo de cuota LLM. |
| Frontend y gráficos | Astro v5 estático + `chart.js` empaquetado vía npm | Carga de `Chart.js` y utilidades desde CDNs públicos o SPA React completa | Bundle cliente mínimo y funcionamiento offline sin riesgo de caída o bloqueo CSP de terceros. |

---

## 4. Riesgos, Límites y Mitigaciones

| Riesgo / Límite | Impacto | Mitigación Implementada |
| :--- | :--- | :--- |
| Agotamiento de cuota (`429`) en Gemini 3.6 | Interrupción de la clasificación o reintentos en cascada | Rotación round-robin en `GeminiKeyRotator` sobre Gemini 3.6 (`gemini-3.6-flash`), cooldown por clave, circuit breaker cuando todas las claves están en cooldown y respaldo heurístico por palabras clave. No utiliza versiones distintas a 3.6. |
| Bloqueo anti-bot, paywalls o redirecciones (`news.google.com`) | Artículo sin cuerpo completo en Markdown | El clasificador combina el resumen de búsqueda y hasta 10 titulares agrupados de `mentions` junto con el contenido disponible. |
| Consumo excesivo de recursos o bloqueo por IPs internas | Saturación de Chromium/LLM o acceso SSRF desde URLs externas | Validación estricta (`1 <= max_results <= 15`, `query` de 1 a 200 caracteres) en `/api/analyze` y `/api/stream`, semáforo de concurrencia (`MAX_CONCURRENT_CRAWLS = 3`) y bloqueo de esquemas no HTTP(S) e IPs privadas/loopback. |
| Inyección HTML/URL desde titulares o salidas del LLM | Ejecución de script o enlaces maliciosos en el panel web | Escape HTML de todos los campos dinámicos y validación estricta de esquema `http://`/`https://` antes de renderizar enlaces en `frontend/src/pages/index.astro`. |
| Despliegue como servidor único local | Exposición innecesaria si se abre a la red | Por defecto `server.py` enlaza `127.0.0.1` sin recarga automática (`reload`) y CORS restringido a orígenes locales (`localhost`/`127.0.0.1`). |


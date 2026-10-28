from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import LatencyAndTracingMiddleware
from app.api.router import router as api_router
from app.cache import cache_manager
from app.config import get_settings
from app.database import init_db
from app.logger import logger

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup & shutdown hooks."""
    logger.info(
        f"Starting {settings.app_name} on {settings.host}:{settings.port}",
        extra={"app": settings.app_name, "version": settings.app_version},
    )
    # Initialize relational database tables
    await init_db()
    # Connect to Redis
    await cache_manager.connect()

    yield

    logger.info("Shutting down Onchain caching proxy...")
    await cache_manager.disconnect()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Enterprise Caching Proxy and Data Access Layer in Python using FastAPI, Redis, and SQLAlchemy with Read-Through/Write-Through and Redlock stampede protection.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Middleware
app.add_middleware(LatencyAndTracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router)

# Mount frontend web artifacts
web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    async def serve_index():
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Onchain Caching Proxy UI</h1>")

    @app.get("/style.css", include_in_schema=False)
    async def serve_style():
        style_file = web_dir / "style.css"
        if style_file.exists():
            return FileResponse(style_file, media_type="text/css")
        return HTMLResponse("", status_code=404)


@app.get("/healthz", tags=["System"])
async def healthz():
    return {"status": "ok", "service": "onchain", "version": settings.app_version}

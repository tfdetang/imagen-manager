"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.utils.storage import ImageStorage


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: cleanup old files
    storage = ImageStorage(settings.storage_dir, settings.base_url)
    storage.cleanup_old_files(settings.cleanup_hours)
    print(f"Cleaned up files older than {settings.cleanup_hours} hours")

    yield

    # Shutdown: nothing to cleanup


app = FastAPI(
    title="Imagen API",
    description="OpenAI-compatible API for Google Gemini Imagen 3",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境建议指定具体域名
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法（包括 OPTIONS）
    allow_headers=["*"],  # 允许所有请求头
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include API routes
app.include_router(router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Imagen API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/v1/health",
    }

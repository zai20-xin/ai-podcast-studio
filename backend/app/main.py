"""FastAPI 应用入口"""
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.api import projects, podcast, voices, settings, script
from app.config import AUDIO_DIR, VOICES_DIR

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Podcast Studio",
    description="AI 播客制作工具",
    version="1.1.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# 开发默认；生产请改为白名单 origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(podcast.router)
app.include_router(voices.router)
app.include_router(settings.router)
app.include_router(script.router)

app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")
app.mount("/voices", StaticFiles(directory=str(VOICES_DIR)), name="voices")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {"message": "AI Podcast Studio API", "version": "1.1.0"}

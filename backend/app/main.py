"""FastAPI 应用入口"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.api import projects, podcast, voices, settings, script
from app.services.audio import AudioService
from app.config import (
    AUDIO_DIR,
    VOICES_DIR,
    INTERMEDIATE_KEEP_HOURS,
    CLEANUP_INTERVAL_HOURS,
)

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


_cleanup_task: asyncio.Task | None = None


async def _periodic_cleanup() -> None:
    """周期性清理中间产物。

    此前 cleanup_old_intermediates / cleanup_temp 定义了却从未被调用，
    试听产生的 tts_*.wav 会一直堆积在 data/audio 下。
    """
    audio = AudioService()
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
            removed_intermediate = await asyncio.to_thread(
                audio.cleanup_old_intermediates, INTERMEDIATE_KEEP_HOURS
            )
            removed_temp = await asyncio.to_thread(audio.cleanup_temp)
            if removed_intermediate or removed_temp:
                logger.info(
                    "周期清理：中间音频 %d 个、临时文件 %d 个",
                    removed_intermediate,
                    removed_temp,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("周期清理失败")


@app.on_event("startup")
async def startup():
    init_db()
    audio = AudioService()
    try:
        removed = await asyncio.to_thread(
            audio.cleanup_old_intermediates, INTERMEDIATE_KEEP_HOURS
        )
        removed += await asyncio.to_thread(audio.cleanup_temp)
        if removed:
            logger.info("启动清理：移除 %d 个过期中间文件", removed)
    except Exception:
        logger.exception("启动清理失败")

    global _cleanup_task
    _cleanup_task = asyncio.create_task(_periodic_cleanup())


@app.on_event("shutdown")
async def shutdown():
    # 先取消在飞合成，避免进程退出时留下半截 segments_json / 心跳
    try:
        from app.services import task_runner

        task_runner.cancel_all()
    except Exception:
        logger.exception("shutdown 取消合成任务失败")
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass


@app.get("/")
def root():
    return {"message": "AI Podcast Studio API", "version": "1.1.0"}

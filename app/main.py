from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.db.database import init_db
from app.services.executor_lock import get_executor_lock_status, release_executor_lock
from app.services.job_manager import initialize_job_manager


settings.static_files_dir.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = Path(__file__).with_name("frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    initialize_job_manager()
    lock = get_executor_lock_status()
    if lock.get("locked") and lock.get("stale_candidate"):
        release_executor_lock("后端启动时发现执行端旧锁超过阈值，自动释放。")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router, prefix="/api")
app.mount("/files", StaticFiles(directory=settings.static_files_dir), name="files")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


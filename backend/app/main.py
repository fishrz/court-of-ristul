import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.db import Base, SessionLocal, engine
from app.poller import polling_loop
from app.routers.matches import router as matches_router
from app.routers.players import router as players_router
from app.routers.trials import router as trials_router
from app.routers.trials import settle_overdue_trials


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # 内存中的 deadline 定时器不会跨重启存活。补结算已超时的审判，
    # 并为尚未到点的重建定时器，否则重启会让进行中的审判永远停在 voting。
    await settle_overdue_trials()
    poller_task = asyncio.create_task(polling_loop(SessionLocal))
    yield
    poller_task.cancel()
    with suppress(asyncio.CancelledError):
        await poller_task
    await engine.dispose()


app = FastAPI(title="瑞斯图尔法庭", lifespan=lifespan)
# 开发期前端跑在静态服务（:4311），后端在 :8000，必须放行跨域。
# 生产同源部署时这条不生效也无害。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4311",
        "http://localhost:4311",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches_router)
app.include_router(players_router)
app.include_router(trials_router)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/join")
async def join_page() -> FileResponse:
    """Steam ID 登记页（T5）。给五黑朋友填 ID 用。"""
    return FileResponse(STATIC_DIR / "join.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.db import Base, SessionLocal, engine
from app.poller import polling_loop
from app.routers.dossier import router as dossier_router
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
# 生产同源部署（Caddy 反代）其实不触发 CORS；这里的白名单是为了
# 万一前端被单独托管到别的域时仍可用。开发期前端跑在静态服务，
# 端口和后端 :8010 不同，必须放行跨域。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ristul.icu",
        "https://www.ristul.icu",
    ],
    # 本地开发：静态预览端口经常换，用正则放行任意回环端口，
    # 免得每换一个端口就要改这里。生产域名仍走上面的白名单。
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches_router)
app.include_router(players_router)
app.include_router(trials_router)
app.include_router(dossier_router)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/join")
async def join_page() -> FileResponse:
    """Steam ID 登记页（T5）。给五黑朋友填 ID 用。"""
    return FileResponse(STATIC_DIR / "join.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.db import Base, SessionLocal, engine
from app.poller import polling_loop
from app.routers.matches import router as matches_router
from app.routers.players import router as players_router
from app.routers.trials import router as trials_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    poller_task = asyncio.create_task(polling_loop(SessionLocal))
    yield
    poller_task.cancel()
    with suppress(asyncio.CancelledError):
        await poller_task
    await engine.dispose()


app = FastAPI(title="瑞斯图尔法庭", lifespan=lifespan)
app.include_router(matches_router)
app.include_router(players_router)
app.include_router(trials_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

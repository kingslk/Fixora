from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .http.api import router
from .tasks.recovery import recover_interrupted_tasks


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 上次进程死在活动态的 Attempt 标失败，并清掉残留 worktree。
    recover_interrupted_tasks()
    yield


app = FastAPI(
    title="Fixora API",
    version="0.1.0",
    description="HTTP 契约见 `fixora.http.protocol`。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

from __future__ import annotations

import asyncio

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from ..config import get_settings
from ..db import SessionLocal
from .workflow import commit_task, run_task

broker = RedisBroker(url=get_settings().redis_url)
dramatiq.set_broker(broker)


@dramatiq.actor(max_retries=0, time_limit=3_600_000)
def run_attempt_actor(attempt_id: int) -> None:
    """参数是 attempt_id。旧队列里的 task_id 走 run_task_actor 被丢掉。"""
    with SessionLocal() as db:
        asyncio.run(run_task(db, attempt_id))


@dramatiq.actor(max_retries=0, time_limit=600_000)
def commit_attempt_actor(attempt_id: int) -> None:
    with SessionLocal() as db:
        reanalyze = commit_task(db, attempt_id)
    if reanalyze:
        run_attempt_actor.send(attempt_id)


@dramatiq.actor(max_retries=0, time_limit=3_600_000)
def run_task_actor(task_id: int) -> None:
    # 旧队列消息以 task_id 入队；丢弃以免误跑新 Attempt。
    return


@dramatiq.actor(max_retries=0, time_limit=600_000)
def commit_task_actor(task_id: int) -> None:
    return


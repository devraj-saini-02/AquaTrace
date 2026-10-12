import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def schedule_baselining_flip(node_id: str, delay_s: int, flip_coro_factory) -> None:
    """One-off job, fires `delay_s` seconds after registration ack, per node_id."""
    run_date = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
    scheduler.add_job(
        flip_coro_factory,
        "date",
        run_date=run_date,
        args=[node_id],
        id=f"baselining-flip-{node_id}-{run_date.timestamp()}",
        misfire_grace_time=None,
    )

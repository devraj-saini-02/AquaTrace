"""Storage retention (how long raw rows are kept) — deliberately distinct from the tracing
engine's lookback window (how far back a trace looks). See README for why these must not be
conflated: a short RETENTION_HOURS must never eat data the tracing engine still needs."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.config import get_settings
from app.database import SessionLocal
from app.models import SummaryWindow

logger = logging.getLogger(__name__)


async def run_retention_cleanup() -> None:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.retention_hours)
    async with SessionLocal() as session:
        result = await session.execute(delete(SummaryWindow).where(SummaryWindow.t_end < cutoff))
        await session.commit()
        if result.rowcount:
            logger.info("retention cleanup: removed %s summary_windows older than %s", result.rowcount, cutoff)

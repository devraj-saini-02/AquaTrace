"""§10: a faulted sensor's corrupted reading must not be trusted by the mass-balance engine.
Exercises get_mass()/_has_coverage() against real DB rows with different sensor_health values.

All three scenarios run inside one test function sharing one session/event-loop lifecycle:
splitting them across separate pytest-asyncio tests hits a known asyncpg gotcha where a
module-level engine's connection pool, created lazily under the first test's event loop, can't
be safely reused once pytest-asyncio hands the next test a fresh event loop."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models import ContaminantReadingRow, Node, NodeStatus, SummaryWindow
from app.tracing.engine import _has_coverage, get_mass


async def _clean(session, node_id: str):
    # SummaryWindow -> ContaminantReadingRow cascades (ondelete=CASCADE); Node doesn't cascade
    # to SummaryWindow, so delete windows first, then the node, to keep reruns idempotent.
    await session.execute(delete(SummaryWindow).where(SummaryWindow.node_id == node_id))
    await session.execute(delete(Node).where(Node.node_id == node_id))
    await session.commit()


async def _seed_reading(session, node_id: str, seq: int, t_end: datetime, mass_g: float, sensor_health: str):
    summary = SummaryWindow(
        node_id=node_id, seq=seq, t_start=t_end - timedelta(minutes=12), t_end=t_end,
        level_m=0.3, speed_mps=0.5, q_m3s=0.4,
    )
    session.add(summary)
    await session.flush()
    session.add(
        ContaminantReadingRow(
            summary_id=summary.id, contaminant_id="tss", mean=25.0, max=25.0, median=25.0, std=1.0,
            flux_g_s=mass_g / 720.0, mass_g=mass_g, n=24, sensor_health=sensor_health,
            rule_state="SAFE", final_state="SAFE", model_ver="tss_v1",
        )
    )


@pytest.mark.asyncio
async def test_mass_balance_excludes_unhealthy_readings():
    t0 = datetime.now(timezone.utc)
    window = (t0 - timedelta(hours=1), t0 + timedelta(minutes=1))

    async with SessionLocal() as session:
        node_ok_and_failed = "TEST-HEALTH-01"
        await _clean(session, node_ok_and_failed)
        session.add(Node(node_id=node_ok_and_failed, latitude=28.6, longitude=77.2, breadth_m=3.0, status=NodeStatus.active))
        await session.flush()
        await _seed_reading(session, node_ok_and_failed, 0, t0 - timedelta(minutes=24), mass_g=1000.0, sensor_health="OK")
        await _seed_reading(session, node_ok_and_failed, 1, t0 - timedelta(minutes=12), mass_g=99999.0, sensor_health="FAILED")
        await _seed_reading(session, node_ok_and_failed, 2, t0, mass_g=1100.0, sensor_health="OK")
        await session.commit()

        # the FAILED reading's wildly inflated 99999g must not be counted
        mass = await get_mass(session, node_ok_and_failed, "tss", *window)
        assert mass == pytest.approx(1000.0 + 1100.0)
        assert await _has_coverage(session, node_ok_and_failed, "tss", *window) is True

        node_only_unhealthy = "TEST-HEALTH-02"
        await _clean(session, node_only_unhealthy)
        session.add(Node(node_id=node_only_unhealthy, latitude=28.6, longitude=77.2, breadth_m=3.0, status=NodeStatus.active))
        await session.flush()
        await _seed_reading(session, node_only_unhealthy, 0, t0, mass_g=50.0, sensor_health="SUSPECT")
        await session.commit()

        # nothing trustworthy in the window at all -- must read as "no coverage", not "mass=0"
        assert await get_mass(session, node_only_unhealthy, "tss", *window) == 0.0
        assert await _has_coverage(session, node_only_unhealthy, "tss", *window) is False

        await _clean(session, node_ok_and_failed)
        await _clean(session, node_only_unhealthy)

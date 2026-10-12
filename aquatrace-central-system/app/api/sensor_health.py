from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import SensorHealthEvent
from app.rest_schemas import SensorHealthEventOut

router = APIRouter(tags=["sensor-health"])


@router.get("/sensor-health-events", response_model=list[SensorHealthEventOut])
async def list_sensor_health_events(session: AsyncSession = Depends(get_session)):
    """Backs the dashboard's Sensor Health / Replacement Queue (§10.2c) — every SensorHealthEvent
    ever opened, newest first, so the UI can show both currently-open (resolved_at is null) and
    historical degradation."""
    rows = (
        (await session.execute(select(SensorHealthEvent).order_by(SensorHealthEvent.detected_at.desc())))
        .scalars()
        .all()
    )
    return [
        SensorHealthEventOut(
            id=str(r.id),
            node_id=r.node_id,
            contaminant_id=r.contaminant_id,
            health_state=r.health_state,
            detected_at=r.detected_at,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]

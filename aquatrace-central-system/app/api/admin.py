"""Administrative actions, kept separate from app/api/sim_proxy.py on purpose: sim_proxy is a
deliberately dumb passthrough that must never touch the database (see its own docstring). This
router does touch it, but the action it exposes -- wipe topology/history -- has nothing
simulation-specific about it; it's the same "start a fleet over from a clean slate" operation a
real redeployment would need too."""
from sqlalchemy import delete

from fastapi import APIRouter

from app.database import SessionLocal
from app.models import (
    Alert,
    ContaminantReadingRow,
    Edge,
    Incident,
    IncidentNode,
    ModelVersionRow,
    Node,
    SensorHealthEvent,
    SummaryWindow,
)

router = APIRouter(tags=["admin"])


@router.post("/admin/reset-topology")
async def reset_topology():
    """Deletes every node, edge, and everything that references a node_id -- registration is
    keyed by a deterministic node_id (SIM-N001, ...), so a fresh simulation run whose nodes land
    at different coordinates than a previous run doesn't overwrite the old row, it gets renamed
    to SIM-N001-2 (see resolve_node_id) and the stale one sits there forever, both showing up in
    GET /nodes and never receiving new data again. Call this before starting a new run to avoid
    accumulating orphaned nodes/edges from every previous run across the DB's lifetime."""
    async with SessionLocal() as session:
        # Children before parents -- explicit regardless of which FKs have DB-level ON DELETE
        # CASCADE, so this doesn't depend on migration history being exactly right.
        await session.execute(delete(ContaminantReadingRow))
        await session.execute(delete(IncidentNode))
        await session.execute(delete(SummaryWindow))
        await session.execute(delete(Alert))
        await session.execute(delete(Edge))
        await session.execute(delete(ModelVersionRow))
        await session.execute(delete(SensorHealthEvent))
        await session.execute(delete(Incident))
        await session.execute(delete(Node))
        await session.commit()
    return {"status": "reset"}

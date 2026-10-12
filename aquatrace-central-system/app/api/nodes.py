from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import ContaminantReadingRow, Edge, Node, SummaryWindow
from app.rest_schemas import ContaminantReadingOut, LatestSummaryOut, NodeDetailOut, NodeOut

router = APIRouter(tags=["nodes"])


@router.get("/nodes", response_model=list[NodeOut])
async def list_nodes(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Node))).scalars().all()
    return [NodeOut.model_validate(n, from_attributes=True) for n in rows]


@router.get("/nodes/{node_id}", response_model=NodeDetailOut)
async def get_node(node_id: str, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")

    parent_ids = (
        (await session.execute(select(Edge.parent_node_id).where(Edge.child_node_id == node_id)))
        .scalars()
        .all()
    )
    child_ids = (
        (await session.execute(select(Edge.child_node_id).where(Edge.parent_node_id == node_id)))
        .scalars()
        .all()
    )

    latest = (
        await session.execute(
            select(SummaryWindow)
            .where(SummaryWindow.node_id == node_id)
            .order_by(SummaryWindow.t_end.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    latest_summary = None
    if latest is not None:
        readings = (
            (await session.execute(select(ContaminantReadingRow).where(ContaminantReadingRow.summary_id == latest.id)))
            .scalars()
            .all()
        )
        latest_summary = LatestSummaryOut(
            seq=latest.seq,
            t_start=latest.t_start,
            t_end=latest.t_end,
            level_m=latest.level_m,
            speed_mps=latest.speed_mps,
            q_m3s=latest.q_m3s,
            received_at=latest.received_at,
            contaminants=[
                ContaminantReadingOut(
                    contaminant_id=r.contaminant_id,
                    mean=r.mean,
                    max=r.max,
                    median=r.median,
                    std=r.std,
                    flux_g_s=r.flux_g_s,
                    mass_g=r.mass_g,
                    t_max=r.t_max,
                    n=r.n,
                    sensor_health=r.sensor_health,
                    rule_state=r.rule_state,
                    ml_anomaly=r.ml_anomaly,
                    final_state=r.final_state,
                    model_ver=r.model_ver,
                )
                for r in readings
            ],
        )

    return NodeDetailOut(
        node_id=node.node_id,
        latitude=node.latitude,
        longitude=node.longitude,
        breadth_m=node.breadth_m,
        invert_level_m=node.invert_level_m,
        status=node.status,
        registered_at=node.registered_at,
        parent_ids=list(parent_ids),
        child_ids=list(child_ids),
        latest_summary=latest_summary,
    )

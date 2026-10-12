from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import ContaminantReadingRow, Edge, Incident, SummaryWindow
from app.rest_schemas import (
    EdgeTimeseriesPoint,
    IncidentDetailOut,
    IncidentOut,
    IncidentTimeseriesOut,
    NodeTimeseriesPoint,
)
from app.tracing.engine import run_tracing

router = APIRouter(tags=["incidents"])


def _to_out(incident: Incident, node_ids: list[str]) -> IncidentDetailOut:
    causes = incident.candidate_causes or []
    top_confidence = causes[0]["confidence"] if causes else None
    return IncidentDetailOut(
        id=str(incident.id),
        status=incident.status,
        contaminant_id=incident.contaminant_id,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        affected_node_count=len(node_ids),
        top_confidence=top_confidence,
        candidate_causes=causes,
        node_ids=node_ids,
    )


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(session: AsyncSession = Depends(get_session)):
    rows = (
        (await session.execute(select(Incident).options(selectinload(Incident.nodes)).order_by(Incident.updated_at.desc())))
        .scalars()
        .all()
    )
    return [_to_out(i, [n.node_id for n in i.nodes]) for i in rows]


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
async def get_incident(incident_id: str, session: AsyncSession = Depends(get_session)):
    incident = (
        await session.execute(select(Incident).options(selectinload(Incident.nodes)).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return _to_out(incident, [n.node_id for n in incident.nodes])


@router.get("/incidents/{incident_id}/timeseries", response_model=IncidentTimeseriesOut)
async def get_incident_timeseries(incident_id: str, session: AsyncSession = Depends(get_session)):
    """Drives dashboard/'s plume-playback animation directly from the same mass/flux values the
    tracing engine already computed (§4.5) — no client-side re-derivation."""
    incident = (
        await session.execute(select(Incident).options(selectinload(Incident.nodes)).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    if incident.window_t_start is None or incident.window_t_end is None:
        return IncidentTimeseriesOut(incident_id=str(incident.id), t_start=incident.created_at, t_end=incident.updated_at, nodes=[], edges=[])

    node_ids = [n.node_id for n in incident.nodes]
    rows = await session.execute(
        select(SummaryWindow.node_id, SummaryWindow.t_end, ContaminantReadingRow.mean, ContaminantReadingRow.mass_g, ContaminantReadingRow.flux_g_s, ContaminantReadingRow.final_state)
        .join(ContaminantReadingRow, ContaminantReadingRow.summary_id == SummaryWindow.id)
        .where(
            SummaryWindow.node_id.in_(node_ids),
            ContaminantReadingRow.contaminant_id == incident.contaminant_id,
            SummaryWindow.t_end >= incident.window_t_start,
            SummaryWindow.t_end <= incident.window_t_end,
        )
        .order_by(SummaryWindow.t_end)
    )

    node_points: list[NodeTimeseriesPoint] = []
    flux_by_node: dict[str, list[tuple]] = {}
    for node_id, t, mean, mass_g, flux_g_s, final_state in rows:
        node_points.append(
            NodeTimeseriesPoint(t=t, node_id=node_id, concentration=mean, mass_g=mass_g or 0.0, flux_g_s=flux_g_s or 0.0, final_state=final_state)
        )
        flux_by_node.setdefault(node_id, []).append((t, flux_g_s or 0.0))

    edges = (
        (await session.execute(select(Edge).where(Edge.parent_node_id.in_(node_ids), Edge.child_node_id.in_(node_ids))))
        .scalars()
        .all()
    )
    edge_points: list[EdgeTimeseriesPoint] = []
    for e in edges:
        # the mass currently traveling this edge is approximated by the upstream node's own
        # reported flux at each t -- reusing the ingested value directly, not re-deriving it
        for t, flux in flux_by_node.get(e.parent_node_id, []):
            edge_points.append(EdgeTimeseriesPoint(t=t, parent_id=e.parent_node_id, child_id=e.child_node_id, flux_g_s=flux))

    return IncidentTimeseriesOut(
        incident_id=str(incident.id), t_start=incident.window_t_start, t_end=incident.window_t_end, nodes=node_points, edges=edge_points
    )


@router.post("/incidents/trace", response_model=list[IncidentOut])
async def trigger_trace(contaminant_id: str, window: Optional[float] = None, session: AsyncSession = Depends(get_session)):
    incidents = await run_tracing(contaminant_id, window_seconds=window)
    # run_tracing appends once per node that triggered record_incident_candidate, and nodes
    # sharing one episode merge into the same Incident -- dedupe before re-fetching each one
    seen_ids = {incident.id for incident in incidents}
    out = []
    for incident_id in seen_ids:
        refreshed = (
            await session.execute(select(Incident).options(selectinload(Incident.nodes)).where(Incident.id == incident_id))
        ).scalar_one()
        out.append(_to_out(refreshed, [n.node_id for n in refreshed.nodes]))
    out.sort(key=lambda i: i.updated_at, reverse=True)
    return out

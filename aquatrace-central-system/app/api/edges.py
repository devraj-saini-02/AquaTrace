from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Edge
from app.rest_schemas import EdgeOut

router = APIRouter(tags=["edges"])


@router.get("/edges", response_model=list[EdgeOut])
async def list_edges(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Edge))).scalars().all()
    return [
        EdgeOut(
            id=str(e.id),
            parent_node_id=e.parent_node_id,
            child_node_id=e.child_node_id,
            length_m=e.length_m,
            manning_n=e.manning_n,
            slope=e.slope,
            tau_base_s=e.tau_base_s,
            validated=e.validated,
        )
        for e in rows
    ]

"""Simulation Control Proxy (§4.6) — DEMO-ONLY, deliberately isolated from everything else in
this service. dashboard/ must never talk to node/ directly; this router exists purely so
dashboard/ still only ever has one API base URL (central-system/'s) to call for the "press
start" simulation controls that node/'s orchestrator actually owns.

This file does nothing but forward HTTP calls verbatim and return the response. It must NEVER
touch the database, the MQTT client, or the tracing engine — central-system/'s real ingestion
and tracing pipeline has zero awareness that a simulation exists anywhere in the loop, and this
module is the one deliberate, narrowly-scoped exception to that."""
import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import get_settings

router = APIRouter(tags=["simulation-proxy"])


async def _forward(request: Request, method: str, path: str) -> Response:
    settings = get_settings()
    url = f"{settings.node_orchestrator_url}{path}"
    body = await request.body()
    try:
        async with httpx.AsyncClient() as client:
            upstream = await client.request(method, url, content=body, params=dict(request.query_params), timeout=15.0)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"node orchestrator unreachable at {url}: {e}")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))


@router.post("/simulation/start")
async def simulation_start(request: Request):
    return await _forward(request, "POST", "/simulation/start")


@router.post("/simulation/stop")
async def simulation_stop(request: Request):
    return await _forward(request, "POST", "/simulation/stop")


@router.post("/simulation/inject-event")
async def simulation_inject_event(request: Request):
    return await _forward(request, "POST", "/simulation/inject-event")


@router.post("/simulation/inject-fault")
async def simulation_inject_fault(request: Request):
    return await _forward(request, "POST", "/simulation/inject-fault")


@router.get("/simulation/status")
async def simulation_status(request: Request):
    return await _forward(request, "GET", "/simulation/status")

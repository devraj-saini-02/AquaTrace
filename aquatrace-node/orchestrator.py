"""Simulation Orchestrator (§5.1c) — the ONLY place in node/ that knows a simulation is
happening. central-system/ never calls this API directly and is never called by it; it only
ever sees the MQTT traffic each Virtual Node produces once started. dashboard/'s Simulation
Control Panel reaches this indirectly, through central-system/'s isolated proxy router."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import Settings, get_settings
from core.virtual_node import VirtualNode
from ml.inference import ModelStore
from registry import get_registry
from simulator.clock import SimClock
from simulator.world import WorldSimulator

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


class StartRequest(BaseModel):
    node_count: int = Field(default_factory=lambda: get_settings().default_node_count)
    sim_time_scale: float = Field(default_factory=lambda: get_settings().sim_time_scale)
    seed: int = Field(default_factory=lambda: get_settings().sim_seed)
    auto_inject_demo_events: bool = True


class InjectEventRequest(BaseModel):
    node_id: str
    contaminant_id: str
    magnitude: float
    start_offset_s: float = 60.0
    duration_s: float = 600.0
    onset_sharpness: float = 1.0


class InjectFaultRequest(BaseModel):
    node_id: str
    contaminant_id: str
    fault_type: Literal["stuck", "dropout", "spike"]
    start_offset_s: float = 0.0
    duration_s: float = 300.0


class StatusResponse(BaseModel):
    running: bool
    sim_time: Optional[datetime]
    node_count: int
    node_ids: list[str]
    seed: Optional[int]
    sim_time_scale: Optional[float]


class SimulationManager:
    def __init__(self):
        self.world: Optional[WorldSimulator] = None
        self.clock: Optional[SimClock] = None
        self.nodes: dict[str, VirtualNode] = {}
        self.tasks: list[asyncio.Task] = []
        self.running = False
        self.seed: Optional[int] = None
        self.run_settings: Optional[Settings] = None
        self.registry = get_registry()

    async def start(self, node_count: int, sim_time_scale: float, seed: int, auto_inject_demo_events: bool = True) -> None:
        if self.running:
            raise RuntimeError("simulation already running; call /simulation/stop first")

        self.run_settings = get_settings().model_copy(update={"sim_time_scale": sim_time_scale})
        self.seed = seed
        self.clock = SimClock(time_scale=sim_time_scale)
        self.clock.start()
        self.world = WorldSimulator(node_count=node_count, seed=seed, clock=self.clock, registry=self.registry)

        model_store = ModelStore(self.run_settings)
        self.nodes = {
            node_id: VirtualNode(self.world, self.clock, self.registry, self.run_settings, node_id, model_store)
            for node_id in self.world.node_ids()
        }
        self.tasks = [asyncio.create_task(vn.run()) for vn in self.nodes.values()]
        self.running = True
        logger.info("simulation started: node_count=%s seed=%s sim_time_scale=%s", node_count, seed, sim_time_scale)
        if auto_inject_demo_events:
            self._schedule_demo_events()

    def _schedule_demo_events(self) -> None:
        # Guarantees a couple of nodes visibly cross into WARN/RED shortly after start, so a live
        # demo has real alerts and a real incident to point at without operating the manual
        # inject-event form in front of an audience. Magnitudes are sized well past each
        # contaminant's limit_warn (config/registry.json) so they land clearly in RED rather than
        # borderline WARN. Uses the same World.inject_event() the manual "Inject Discharge"
        # control calls -- nothing here is a special demo-only code path.
        node_ids = self.world.node_ids()
        if len(node_ids) < 2:
            return
        # Magnitude follows a sin(pi * phase) ramp that peaks at duration_s/2 and returns to 0 at
        # duration_s -- duration_s=14400 (4 simulated hours) puts the RED-level window at roughly
        # 25-100 real seconds in at the default 120x time scale, comfortably overlapping
        # central-system's 90s periodic tracing scan (see app/scheduler.py) so an Incident
        # actually gets created automatically, not just a colored dot on the map.
        demo_events = [
            (node_ids[min(1, len(node_ids) - 1)], "ammoniacal_n", 80.0),  # baseline 8, limit_warn 50
            (node_ids[min(2, len(node_ids) - 1)], "cod", 280.0),  # baseline 50, limit_warn 250
        ]
        for node_id, contaminant_id, magnitude in demo_events:
            start_time = self.clock.now() + timedelta(seconds=60)
            self.world.inject_event(node_id, contaminant_id, magnitude, start_time, duration_s=14400, onset_sharpness=1.0)
        logger.info("auto-injected %s demo contamination event(s) for visible RED/alert state", len(demo_events))

    async def stop(self) -> None:
        if not self.running:
            return
        for vn in self.nodes.values():
            await vn.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.clock:
            self.clock.stop()
        self.nodes = {}
        self.tasks = []
        self.running = False
        logger.info("simulation stopped")


manager = SimulationManager()
app = FastAPI(title="node-orchestrator")


@app.post("/simulation/start")
async def start_simulation(req: StartRequest):
    try:
        await manager.start(req.node_count, req.sim_time_scale, req.seed, req.auto_inject_demo_events)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "started", "node_count": req.node_count, "node_ids": manager.world.node_ids()}


@app.post("/simulation/stop")
async def stop_simulation():
    await manager.stop()
    return {"status": "stopped"}


@app.post("/simulation/inject-event")
async def inject_event(req: InjectEventRequest):
    if not manager.running:
        raise HTTPException(status_code=400, detail="simulation not running")
    start_time = manager.clock.now() + timedelta(seconds=req.start_offset_s)
    try:
        manager.world.inject_event(req.node_id, req.contaminant_id, req.magnitude, start_time, req.duration_s, req.onset_sharpness)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "injected", "node_id": req.node_id, "contaminant_id": req.contaminant_id, "start_time": start_time}


@app.post("/simulation/inject-fault")
async def inject_fault(req: InjectFaultRequest):
    if not manager.running:
        raise HTTPException(status_code=400, detail="simulation not running")
    start_time = manager.clock.now() + timedelta(seconds=req.start_offset_s)
    try:
        manager.world.inject_sensor_fault(req.node_id, req.contaminant_id, req.fault_type, start_time, req.duration_s)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "injected", "node_id": req.node_id, "contaminant_id": req.contaminant_id, "start_time": start_time}


@app.get("/simulation/status", response_model=StatusResponse)
async def status():
    return StatusResponse(
        running=manager.running,
        sim_time=manager.clock.now() if manager.clock else None,
        node_count=len(manager.nodes),
        node_ids=list(manager.nodes.keys()),
        seed=manager.seed,
        sim_time_scale=manager.run_settings.sim_time_scale if manager.run_settings else None,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

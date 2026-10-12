import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import mqtt_ingestion
from app.api import admin, edges, incidents, models, nodes, register, sensor_health, sim_proxy
from app.config import get_settings
from app.mqtt_ingestion import build_ingestion_service
from app.retention import run_retention_cleanup
from app.scheduler import scheduler
from app.tracing.engine import run_periodic_scan, run_tracing

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def _trace_for_alert(contaminant_id: str, timestamp) -> None:
    await run_tracing(contaminant_id, t_end=timestamp)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_ingestion.on_alert_trigger_tracing = _trace_for_alert

    mqtt_service = build_ingestion_service()
    app.state.mqtt_service = mqtt_service
    mqtt_task = asyncio.create_task(mqtt_service.run())

    scheduler.add_job(run_retention_cleanup, "interval", minutes=10, id="retention-cleanup")
    scheduler.add_job(run_periodic_scan, "interval", seconds=90, id="periodic-trace-scan")
    scheduler.start()

    logger.info("central-system startup complete")
    try:
        yield
    finally:
        mqtt_service.stop()
        mqtt_task.cancel()
        scheduler.shutdown(wait=False)


app = FastAPI(title="central-system", lifespan=lifespan)

# CORS_ALLOW_ORIGINS defaults to "*" for the local demo (dashboard/ is a browser app on a
# different origin/port); set it to your deployed dashboard's actual origin(s), comma-separated,
# once you have one -- a wildcard origin is fine for localhost, not for a public deployment.
_cors_origins = (
    ["*"] if settings.cors_allow_origins.strip() == "*" else [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nodes.router)
app.include_router(edges.router)
app.include_router(register.router)
app.include_router(incidents.router)
app.include_router(models.router)
app.include_router(sensor_health.router)
app.include_router(sim_proxy.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}

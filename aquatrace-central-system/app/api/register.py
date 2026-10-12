from fastapi import APIRouter

from app.mqtt_ingestion import process_registration
from app.schemas import RegistrationAck, RegistrationManifest

router = APIRouter(tags=["register"])


@router.post("/register", response_model=RegistrationAck)
async def register(manifest: RegistrationManifest):
    """REST fallback/alternative to MQTT registration (§4.5) — same business logic as the
    nodes/{id}/register MQTT handler, useful for debugging a node in isolation without a
    broker in the loop."""
    return await process_registration(manifest)

"""OTA model push (§4.5, §6). Prefers HTTP download over a shared filesystem mount: the pushed
file is copied into MODEL_FILES_DIR and served back at PUBLIC_URL/models/file/{contaminant_id}/
{version}, which is what ModelUpdateCommand.model_path points nodes at — the closer analog to a
real OTA flow, and just as easy to implement on a single machine."""
import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import ModelVersionRow
from app.mqtt_topics import topic_model_update
from app.rest_schemas import ModelPushRequest, ModelVersionOut
from app.schemas import ModelUpdateCommand

router = APIRouter(tags=["models"])


def _row_to_out(row: ModelVersionRow) -> ModelVersionOut:
    return ModelVersionOut(
        id=str(row.id),
        node_id=row.node_id,
        contaminant_id=row.contaminant_id,
        version=row.version,
        pushed_at=row.pushed_at,
        acked_at=row.acked_at,
        running=row.running,
        shadow_disagreement_rate=row.shadow_disagreement_rate,
    )


@router.post("/models/push", response_model=ModelVersionOut)
async def push_model(req: ModelPushRequest, request: Request, session: AsyncSession = Depends(get_session)):
    source = Path(req.model_path)
    if not source.is_file():
        raise HTTPException(status_code=422, detail=f"model_path {req.model_path!r} is not a readable file")

    settings = get_settings()
    dest_dir = Path(settings.model_files_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{req.contaminant_id}_{req.model_version}.pkl"
    shutil.copyfile(source, dest)
    checksum = hashlib.sha256(dest.read_bytes()).hexdigest()

    row = ModelVersionRow(
        node_id=req.node_id,
        contaminant_id=req.contaminant_id,
        version=req.model_version,
        running=False,
    )
    session.add(row)
    await session.flush()

    download_url = f"{settings.public_url}/models/file/{req.contaminant_id}/{req.model_version}"
    command = ModelUpdateCommand(
        node_id=req.node_id,
        contaminant_id=req.contaminant_id,
        model_version=req.model_version,
        model_path=download_url,
        checksum_sha256=checksum,
        shadow_mode=True,
    )
    mqtt_service = request.app.state.mqtt_service
    await mqtt_service.publish(topic_model_update(req.node_id), command, qos=2)

    await session.commit()
    return _row_to_out(row)


@router.get("/models/file/{contaminant_id}/{version}")
async def download_model_file(contaminant_id: str, version: str):
    path = Path(get_settings().model_files_dir) / f"{contaminant_id}_{version}.pkl"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="model file not found")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.get("/models/{node_id}", response_model=list[ModelVersionOut])
async def list_models_for_node(node_id: str, session: AsyncSession = Depends(get_session)):
    rows = (
        (await session.execute(select(ModelVersionRow).where(ModelVersionRow.node_id == node_id).order_by(ModelVersionRow.pushed_at.desc())))
        .scalars()
        .all()
    )
    return [_row_to_out(r) for r in rows]

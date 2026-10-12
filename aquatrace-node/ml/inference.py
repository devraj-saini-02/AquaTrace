"""Model loading/inference/OTA (§5.1e, §6). Until a model is trained and pushed (training/ +
OTA phases), ModelStore.predict() returns ("SAFE", None) for every contaminant — meaning
final_state is exactly rule_state and virtual nodes run rule-only classification, which is the
deliberate first-checkpoint behavior (see README build order).

download_and_load() only downloads, verifies the checksum, and loads the candidate — it does NOT
hot-swap or decide shadow-mode outcomes. Real shadow-mode evaluation needs a live feature stream
running the candidate alongside the current model for cmd.shadow_duration_s and comparing
predictions tick by tick, which only VirtualNode (core/virtual_node.py) can actually do, since
it's the one ticking against real (simulated) readings and holding the clock. Keeping that
orchestration out of this module avoids a fake shadow_disagreement_rate=0.0 stand-in — the
mistake an earlier version of this file made.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional

import httpx
import joblib

from config import Settings
from protocol.schemas import ModelUpdateCommand

logger = logging.getLogger(__name__)

SHADOW_DISAGREEMENT_THRESHOLD = 0.15


class LoadedModel:
    def __init__(self, version: str, estimator, feature_order: list[str]):
        self.version = version
        self.estimator = estimator
        self.feature_order = feature_order

    def predict(self, features: dict) -> tuple[str, float]:
        x = [[features.get(f, 0.0) for f in self.feature_order]]
        proba = self.estimator.predict_proba(x)[0]
        classes = list(self.estimator.classes_)
        anomaly_score = float(proba[classes.index(1)]) if 1 in classes else float(max(proba))
        state = "RED" if anomaly_score > 0.85 else "WARN" if anomaly_score > 0.5 else "SAFE"
        return state, anomaly_score


class ModelStore:
    """One active model per contaminant, hot-swappable in place via set_active()."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.artifact_dir = Path(settings.model_artifact_dir)
        self.active: dict[str, LoadedModel] = {}
        self._bootstrap_from_disk()

    def _bootstrap_from_disk(self) -> None:
        if not self.artifact_dir.exists():
            return
        for path in self.artifact_dir.glob("*.pkl"):
            try:
                contaminant_id, version = path.stem.rsplit("_", 1)
                bundle = joblib.load(path)
                self.active[contaminant_id] = LoadedModel(version, bundle["estimator"], bundle["feature_order"])
                logger.info("bootstrapped model %s v%s from %s", contaminant_id, version, path)
            except Exception:
                logger.exception("failed to bootstrap model artifact %s", path)

    def predict(self, contaminant_id: str, features: dict) -> tuple[str, Optional[float]]:
        model = self.active.get(contaminant_id)
        if model is None:
            return "SAFE", None
        try:
            return model.predict(features)
        except Exception:
            logger.exception("inference failed for %s, falling back to SAFE", contaminant_id)
            return "SAFE", None

    def set_active(self, contaminant_id: str, model: LoadedModel) -> None:
        self.active[contaminant_id] = model

    async def download_and_load(self, cmd: ModelUpdateCommand) -> Optional[LoadedModel]:
        """Downloads (or reads a local path), verifies the checksum, and loads the candidate —
        returns None (logging why) on any failure, leaving self.active untouched either way."""
        try:
            data = await self._fetch(cmd.model_path)
        except Exception:
            logger.exception("model download failed for %s v%s", cmd.contaminant_id, cmd.model_version)
            return None

        checksum = hashlib.sha256(data).hexdigest()
        if checksum != cmd.checksum_sha256:
            logger.error(
                "checksum mismatch for %s v%s: expected %s got %s",
                cmd.contaminant_id, cmd.model_version, cmd.checksum_sha256, checksum,
            )
            return None

        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        dest = self.artifact_dir / f"{cmd.contaminant_id}_{cmd.model_version}.pkl"
        dest.write_bytes(data)

        try:
            bundle = joblib.load(dest)
            return LoadedModel(cmd.model_version, bundle["estimator"], bundle["feature_order"])
        except Exception:
            logger.exception("failed to load downloaded model %s", dest)
            return None

    async def _fetch(self, model_path: str) -> bytes:
        if model_path.startswith("http://") or model_path.startswith("https://"):
            async with httpx.AsyncClient() as client:
                resp = await client.get(model_path, timeout=30.0)
                resp.raise_for_status()
                return resp.content
        return Path(model_path).read_bytes()

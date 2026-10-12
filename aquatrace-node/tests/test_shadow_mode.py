from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from config import get_settings
from core.virtual_node import ShadowEvaluation, VirtualNode
from ml.inference import LoadedModel, ModelStore
from protocol.schemas import ModelUpdateCommand
from registry import get_registry
from simulator.clock import SimClock
from simulator.world import WorldSimulator

FEATURE_ORDER = ["value", "level_m", "speed_mps", "Q_m3s", "rolling_mean_1h", "rolling_std_1h"]


class _FixedEstimator:
    """Always predicts the given class with high confidence -- deterministic stand-in for a
    trained sklearn model, so the test doesn't depend on real artifacts on disk."""

    def __init__(self, fixed_class: int):
        self.classes_ = [0, 1]
        self._fixed_class = fixed_class

    def predict_proba(self, x):
        return [[0.1, 0.9]] if self._fixed_class == 1 else [[0.9, 0.1]]


def _make_node(tmp_artifact_dir) -> VirtualNode:
    settings = get_settings().model_copy(update={"model_artifact_dir": str(tmp_artifact_dir)})
    registry = get_registry()
    clock = SimClock(time_scale=1.0)
    world = WorldSimulator(node_count=3, seed=1, clock=clock, registry=registry)
    node_id = world.node_ids()[0]
    model_store = ModelStore(settings)  # empty artifact_dir -> no models bootstrapped
    vn = VirtualNode(world, clock, registry, settings, node_id, model_store)
    vn.node_id = node_id
    vn.mqtt.publish = AsyncMock()
    vn._cycle_start = clock.now()  # normally set by _tick_loop(), which these tests bypass
    return vn


@pytest.mark.asyncio
async def test_shadow_evaluation_agreeing_candidate_gets_hot_swapped(tmp_path):
    vn = _make_node(tmp_path)
    # no model loaded for tss -> model_store.predict returns ("SAFE", None) every tick, so a
    # candidate that also always predicts class 0 (-> "SAFE") agrees on every tick
    candidate = LoadedModel(version="v9", estimator=_FixedEstimator(0), feature_order=FEATURE_ORDER)
    cmd = ModelUpdateCommand(
        node_id=vn.node_id, contaminant_id="tss", model_version="v9",
        model_path="unused", checksum_sha256="0" * 64, shadow_mode=True, shadow_duration_s=90,
    )
    vn._shadow["tss"] = ShadowEvaluation(candidate=candidate, cmd=cmd, deadline=vn.clock.now() + timedelta(seconds=90))

    t = vn.clock.now()
    for _ in range(4):  # 4 * 30s = 120s, past the 90s deadline
        t += timedelta(seconds=30)
        await vn._sample_tick(t)

    assert "tss" not in vn._shadow  # finalized and removed
    assert vn.model_store.active["tss"].version == "v9"  # hot-swapped
    vn.mqtt.publish.assert_called()  # ack was sent


@pytest.mark.asyncio
async def test_shadow_evaluation_disagreeing_candidate_does_not_hot_swap(tmp_path):
    vn = _make_node(tmp_path)
    # candidate always predicts class 1 (-> "RED"), which will disagree with the "SAFE" baseline
    # on every tick -> 100% disagreement, well past the verification threshold
    candidate = LoadedModel(version="v9", estimator=_FixedEstimator(1), feature_order=FEATURE_ORDER)
    cmd = ModelUpdateCommand(
        node_id=vn.node_id, contaminant_id="tss", model_version="v9",
        model_path="unused", checksum_sha256="0" * 64, shadow_mode=True, shadow_duration_s=90,
    )
    vn._shadow["tss"] = ShadowEvaluation(candidate=candidate, cmd=cmd, deadline=vn.clock.now() + timedelta(seconds=90))

    t = vn.clock.now()
    for _ in range(4):
        t += timedelta(seconds=30)
        await vn._sample_tick(t)

    assert "tss" not in vn._shadow
    assert "tss" not in vn.model_store.active  # NOT hot-swapped -- still nothing active
    vn.mqtt.publish.assert_called()

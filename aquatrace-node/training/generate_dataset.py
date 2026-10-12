"""Offline dataset generation for the per-contaminant anomaly models (§5.3). NOT part of the
live pipeline — drives the World Simulator over a long synthetic period, sweeping baseline
conditions plus many injected events/faults, and dumps a labeled dataset.

`anomaly_zscore_label` is the honest, disclosed stand-in for a human labeler used throughout this
hackathon build: a z-score against each (node, contaminant)'s own rolling diurnal baseline,
thresholded at |z|>3. There is no real labeling step here — this script says so at the point the
label is computed, not just in a comment nobody reads.

Runs synchronously (no real-time sleeping — ticks are advanced by constructing successive
timestamps directly), so a multi-day synthetic period takes seconds, not days.

Usage: python3 -m training.generate_dataset [--node-count 10] [--sim-days 2] [--events 150]
"""
import argparse
import statistics
import sys
from collections import deque
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from registry import get_registry
from simulator.clock import SimClock
from simulator.world import WORLD_TICK_INTERVAL_S, WorldSimulator

ROLLING_WINDOW_TICKS = 120  # 1 hour at WORLD_TICK_INTERVAL_S=30s
ZSCORE_THRESHOLD = 3.0
MIN_ROLLING_SAMPLES = 10


def _schedule_events(world: WorldSimulator, rng: np.random.Generator, t0, total_seconds: float, n_events: int, n_faults: int):
    node_ids = world.node_ids()
    transportable = [c for c in world.registry.all_ids() if c not in ("ph", "temperature")]
    live_ids = world.registry.live_stream_ids()

    for _ in range(n_events):
        node_id = rng.choice(node_ids)
        contaminant_id = rng.choice(transportable)
        magnitude = float(rng.uniform(50.0, 3000.0))
        start_offset = float(rng.uniform(0, max(total_seconds - 3600, 1)))
        duration = float(rng.uniform(300.0, 3600.0))
        sharpness = float(rng.uniform(0.5, 2.5))
        world.inject_event(node_id, contaminant_id, magnitude, t0 + timedelta(seconds=start_offset), duration, sharpness)

    for _ in range(n_faults):
        node_id = rng.choice(node_ids)
        contaminant_id = rng.choice(live_ids)
        fault_type = rng.choice(["stuck", "dropout", "spike"])
        start_offset = float(rng.uniform(0, max(total_seconds - 3600, 1)))
        duration = float(rng.uniform(120.0, 1800.0))
        world.inject_sensor_fault(node_id, contaminant_id, fault_type, t0 + timedelta(seconds=start_offset), duration)


def _active_injection_origin(world: WorldSimulator, node_id: str, contaminant_id: str, t) -> str | None:
    """Origin node of an event actively injecting AT this exact node/contaminant/time — labels
    provenance at the injection point only, not full downstream reachability (a natural
    extension, not implemented here: see node/README's disclosed-simplifications section)."""
    for ev in world.events:
        if ev.node_id != node_id or ev.contaminant_id != contaminant_id:
            continue
        elapsed = (t - ev.start_time).total_seconds()
        if 0 <= elapsed <= ev.duration_s:
            return ev.node_id
    return None


def generate(node_count: int, seed: int, sim_days: float, n_events: int, n_faults: int) -> pd.DataFrame:
    registry = get_registry()
    clock = SimClock(time_scale=1.0)  # not advanced by real time here; ticks are driven manually
    world = WorldSimulator(node_count=node_count, seed=seed, clock=clock, registry=registry)

    rng = np.random.default_rng(seed + 1)
    t0 = clock.now()
    total_seconds = sim_days * 86400.0
    n_ticks = int(total_seconds / WORLD_TICK_INTERVAL_S)

    _schedule_events(world, rng, t0, total_seconds, n_events, n_faults)

    live_ids = registry.live_stream_ids()
    node_ids = world.node_ids()
    rolling: dict[tuple[str, str], deque] = {}

    rows = []
    for i in range(n_ticks):
        t = t0 + timedelta(seconds=i * WORLD_TICK_INTERVAL_S)
        for node_id in node_ids:
            level, speed, q = world.get_hydraulics(node_id, t)
            for contaminant_id in live_ids:
                raw = world.get_reading(node_id, contaminant_id, t)
                contam = registry[contaminant_id]
                rule_label = contam.classify(raw)

                key = (node_id, contaminant_id)
                buf = rolling.setdefault(key, deque(maxlen=ROLLING_WINDOW_TICKS))
                if len(buf) >= MIN_ROLLING_SAMPLES:
                    mean = statistics.fmean(buf)
                    std = statistics.pstdev(buf)
                else:
                    mean, std = raw, 0.0
                z = (raw - mean) / std if std > 1e-6 else 0.0
                anomaly_label = int(abs(z) > ZSCORE_THRESHOLD)
                buf.append(raw)

                rows.append(
                    (
                        node_id,
                        t.isoformat(),
                        contaminant_id,
                        raw,
                        level,
                        speed,
                        q,
                        mean,
                        std,
                        rule_label,
                        anomaly_label,
                        _active_injection_origin(world, node_id, contaminant_id, t),
                    )
                )

    return pd.DataFrame(
        rows,
        columns=[
            "node_id",
            "timestamp",
            "contaminant_id",
            "raw_value",
            "level",
            "speed",
            "Q",
            "rolling_mean_1h",
            "rolling_std_1h",
            "rule_label",
            "anomaly_zscore_label",
            "injected_source_node_id_or_null",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--sim-days", type=float, default=2.0)
    parser.add_argument("--events", type=int, default=150)
    parser.add_argument("--faults", type=int, default=60)
    parser.add_argument("--out", type=str, default=str(REPO_ROOT / "training" / "datasets" / "dataset.parquet"))
    args = parser.parse_args()

    df = generate(args.node_count, args.seed, args.sim_days, args.events, args.faults)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"wrote {len(df)} rows to {out_path}")
    print("\nlabel balance by contaminant:")
    print(df.groupby("contaminant_id")["anomaly_zscore_label"].agg(["sum", "count"]))


if __name__ == "__main__":
    main()

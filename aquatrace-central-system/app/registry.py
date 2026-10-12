"""Loads config/registry.json and exposes typed lookups used by classification and the
tracing engine's decay math. Duplicated (not shared/imported) in node/app/registry.py — the two
services agree on shape only because both implement the same spec."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

# Decay class -> first-order rate constant k (1/s), used by the tracing engine's
# M_parent(t - tau) * exp(-k * tau) mass-balance term and by the simulator's transport kernel.
# "none"/"conservative" species get k=0 (no first-order decay in transit).
DECAY_CLASS_RATE_PER_S: dict[str, float] = {
    "none": 0.0,
    "reactive": 5.0e-6,
    "physical_settling": 8.0e-6,
    "biological_reactive": 3.0e-6,
    "biological_uv": 1.2e-5,
}


class Contaminant:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.id: str = raw["id"]
        self.unit: str = raw["unit"]
        self.polarity: str = raw["polarity"]
        self.decay_class: str = raw["decay_class"]
        self.aggregation: str = raw["aggregation"]
        self.conservative: bool = raw.get("conservative", False)
        self.measurement_mode: str = raw["measurement_mode"]
        self.role: Optional[str] = raw.get("role")
        self.cycle_seconds: Optional[int] = raw.get("cycle_seconds")

        self.limit_safe: Optional[float] = raw.get("limit_safe")
        self.limit_warn: Optional[float] = raw.get("limit_warn")
        self.limit_safe_low: Optional[float] = raw.get("limit_safe_low")
        self.limit_safe_high: Optional[float] = raw.get("limit_safe_high")
        self.limit_warn_low: Optional[float] = raw.get("limit_warn_low")
        self.limit_warn_high: Optional[float] = raw.get("limit_warn_high")

    @property
    def decay_rate_per_s(self) -> float:
        if self.conservative:
            return 0.0
        return DECAY_CLASS_RATE_PER_S.get(self.decay_class, 0.0)

    @property
    def is_live_stream(self) -> bool:
        """fecal_coliform (manual_only) never appears in the live MQTT summary stream."""
        return self.measurement_mode != "manual_only"

    def classify(self, value: float) -> str:
        """Rule-based SAFE/WARN/RED against registry thresholds. Handles both single-sided
        (limit_safe/limit_warn) and banded (ph-style) polarity."""
        if self.polarity == "band":
            if self.limit_safe_low is None or self.limit_warn_low is None:
                return "SAFE"
            if self.limit_safe_low <= value <= self.limit_safe_high:
                return "SAFE"
            if self.limit_warn_low <= value <= self.limit_warn_high:
                return "WARN"
            return "RED"

        if self.polarity == "context":
            return "SAFE"

        if self.limit_safe is None or self.limit_warn is None:
            return "SAFE"

        if self.polarity == "low_is_bad":
            if value >= self.limit_safe:
                return "SAFE"
            if value >= self.limit_warn:
                return "WARN"
            return "RED"

        # high_is_bad (default)
        if value <= self.limit_safe:
            return "SAFE"
        if value <= self.limit_warn:
            return "WARN"
        return "RED"


class Registry:
    def __init__(self, path: str):
        raw_list = json.loads(Path(path).read_text())
        self.by_id: dict[str, Contaminant] = {c["id"]: Contaminant(c) for c in raw_list}

    def __getitem__(self, contaminant_id: str) -> Contaminant:
        return self.by_id[contaminant_id]

    def get(self, contaminant_id: str) -> Optional[Contaminant]:
        return self.by_id.get(contaminant_id)

    def live_stream_ids(self) -> list[str]:
        return [c.id for c in self.by_id.values() if c.is_live_stream]


@lru_cache
def get_registry() -> Registry:
    return Registry(get_settings().registry_path)

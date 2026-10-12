"""Sensor plausibility gating (§5.1b). Runs against whatever raw value the world simulator
handed back for a reading — which may already be fault-corrupted (simulator/world.py applies
faults only to the *reported* value, never the true propagated signal) — using only generic
physical-plausibility heuristics a real onboard sensor diagnostic would have: an absolute range
check, a stuck-value (identical readings) check, and an implausible-jump check. This module has
no idea a fault was injected; it's exercising the same logic that would run against a real
broken sensor."""
from collections import deque
from typing import Literal, Optional

HealthState = Literal["OK", "SUSPECT", "FAILED"]

# Generous absolute bounds beyond which a reading is physically impossible for the sensor type,
# not just "heavily polluted" — a real onboard diagnostic would flag these regardless of context.
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "ph": (0.0, 14.0),
    "temperature": (-5.0, 55.0),
    "conductivity": (0.0, 20000.0),
    "tss": (0.0, 5000.0),
    "dissolved_oxygen": (0.0, 20.0),
    "bod": (0.0, 2000.0),
    "cod": (0.0, 5000.0),
    "ammoniacal_n": (0.0, 500.0),
    "fecal_coliform": (0.0, 1.0e7),
    "chromium_vi": (0.0, 50.0),
    "urea": (0.0, 500.0),
}

STUCK_SUSPECT_TICKS = 5
STUCK_FAILED_TICKS = 10
JUMP_MULTIPLE = 6.0  # a tick-over-tick jump this many multiples of the recent rolling mean is
# implausible for a real sensor even accounting for a genuine pollution event; a real spike
# fault (world simulator: ~8x + a large offset) clears this bar, most legitimate injected events
# used in demos should not.
ROLLING_WINDOW = 20


class ContaminantHealthTracker:
    def __init__(self, contaminant_id: str):
        self.contaminant_id = contaminant_id
        self._last_value: Optional[float] = None
        self._stuck_ticks = 0
        self._rolling: deque = deque(maxlen=ROLLING_WINDOW)

    def evaluate(self, value: float) -> HealthState:
        lo, hi = PLAUSIBLE_RANGE.get(self.contaminant_id, (-1e18, 1e18))
        state: HealthState = "OK"

        if value < lo or value > hi:
            state = "FAILED"

        if self._last_value is not None and abs(value - self._last_value) < 1e-9:
            self._stuck_ticks += 1
        else:
            self._stuck_ticks = 0

        if state == "OK" and self._stuck_ticks >= STUCK_FAILED_TICKS:
            state = "FAILED"
        elif state == "OK" and self._stuck_ticks >= STUCK_SUSPECT_TICKS:
            state = "SUSPECT"

        if state == "OK" and self._rolling:
            recent_mean = sum(self._rolling) / len(self._rolling)
            if recent_mean > 1e-6 and abs(value - recent_mean) > JUMP_MULTIPLE * recent_mean:
                state = "SUSPECT"

        self._last_value = value
        if state == "OK":
            # Only ever build the reference baseline from trustworthy readings. A sustained
            # fault (duration >= ROLLING_WINDOW ticks) would otherwise fill this buffer with its
            # own corrupted values, dragging recent_mean up toward the fault level -- at which
            # point *further* fault readings no longer look like a "jump" relative to that now-
            # poisoned baseline, and the gate goes blind to the exact fault it was designed to
            # catch. Freezing the baseline during a flagged episode means it's compared against
            # its last known-good state when the fault clears, which is the correct reference.
            self._rolling.append(value)
        return state

"""Alerting logic (§5.1b): debounce (k-of-n) with hysteresis to clear, a fast path for extreme
single readings that shouldn't wait for debounce, and a CUSUM detector on the residual against a
rolling baseline for sustained loads that never individually cross into WARN/RED."""
from collections import deque
from typing import Optional

from registry import Contaminant

DEBOUNCE_N = 5
DEBOUNCE_K = 3
FAST_PATH_SEVERITY = 1.0  # value is this many multiples of limit_warn's own distance-past-safe
CUSUM_THRESHOLD = 5.0  # in baseline-std units
CUSUM_SLACK = 0.5
BASELINE_WINDOW = 30


def _severity_ratio(contam: Contaminant, value: float) -> float:
    """How far past limit_warn the value sits, as a multiple of limit_warn itself — polarity-
    aware, used only to decide whether a single reading is extreme enough for the fast path."""
    if contam.polarity == "band":
        if contam.limit_warn_low is None:
            return 0.0
        if value < contam.limit_warn_low:
            return (contam.limit_warn_low - value) / max(contam.limit_warn_low, 1e-6)
        if value > contam.limit_warn_high:
            return (value - contam.limit_warn_high) / max(contam.limit_warn_high, 1e-6)
        return 0.0
    if contam.polarity == "context" or contam.limit_warn is None:
        return 0.0
    if contam.polarity == "low_is_bad":
        if value >= contam.limit_warn:
            return 0.0
        return (contam.limit_warn - value) / max(contam.limit_warn, 1e-6)
    if value <= contam.limit_warn:
        return 0.0
    return (value - contam.limit_warn) / max(contam.limit_warn, 1e-6)


class AlertingTracker:
    def __init__(self):
        self._recent_states: deque = deque(maxlen=DEBOUNCE_N)
        self._active = False
        self._cusum = 0.0
        self._baseline: deque = deque(maxlen=BASELINE_WINDOW)

    def update(self, contam: Contaminant, value: float, rule_state: str) -> Optional[tuple[str, str]]:
        """Returns (state, trigger_type) if an alert should fire this tick, else None."""
        if _severity_ratio(contam, value) >= FAST_PATH_SEVERITY:
            self._active = True
            self._recent_states.clear()
            return "RED", "fast_path"

        self._recent_states.append(rule_state)
        warn_or_red = sum(1 for s in self._recent_states if s in ("WARN", "RED"))
        if not self._active and warn_or_red >= DEBOUNCE_K:
            self._active = True
            worst = "RED" if "RED" in self._recent_states else "WARN"
            return worst, "debounced"
        if self._active and warn_or_red == 0:
            self._active = False  # hysteresis: needs a full clean window to clear, not a majority

        result = None
        if self._baseline:
            mean = sum(self._baseline) / len(self._baseline)
            variance = sum((v - mean) ** 2 for v in self._baseline) / len(self._baseline)
            std = variance**0.5
            if std > 1e-6:
                z = (value - mean) / std
                self._cusum = max(0.0, self._cusum + z - CUSUM_SLACK)
                if self._cusum > CUSUM_THRESHOLD:
                    self._cusum = 0.0
                    result = ("WARN", "cusum")
        self._baseline.append(value)
        return result

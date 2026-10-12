from core.alerting import AlertingTracker
from registry import get_registry


def test_fast_path_triggers_on_extreme_single_reading():
    reg = get_registry()
    tss = reg["tss"]  # limit_warn=100
    tracker = AlertingTracker()
    result = tracker.update(tss, value=100.0 * 2.5, rule_state="RED")
    assert result is not None
    state, trigger_type = result
    assert state == "RED"
    assert trigger_type == "fast_path"


def test_single_warn_reading_does_not_trigger_debounce():
    reg = get_registry()
    tss = reg["tss"]
    tracker = AlertingTracker()
    result = tracker.update(tss, value=70.0, rule_state="WARN")
    assert result is None


def test_debounce_triggers_after_k_of_n_warn_readings():
    reg = get_registry()
    tss = reg["tss"]
    tracker = AlertingTracker()
    results = [tracker.update(tss, value=70.0, rule_state="WARN") for _ in range(5)]
    triggered = [r for r in results if r is not None]
    assert len(triggered) == 1
    assert triggered[0] == ("WARN", "debounced")


def test_hysteresis_requires_full_clear_window_before_re_debounce():
    reg = get_registry()
    tss = reg["tss"]
    tracker = AlertingTracker()
    for _ in range(3):
        tracker.update(tss, value=70.0, rule_state="WARN")  # trips debounce
    # a single SAFE reading shouldn't clear an active alert (hysteresis)
    result = tracker.update(tss, value=10.0, rule_state="SAFE")
    assert result is None
    # feed enough SAFE readings to fully clear the window, then trip again
    for _ in range(4):
        tracker.update(tss, value=10.0, rule_state="SAFE")
    results = [tracker.update(tss, value=70.0, rule_state="WARN") for _ in range(3)]
    assert any(r is not None for r in results)


def test_cusum_detects_sustained_sub_threshold_drift():
    reg = get_registry()
    tss = reg["tss"]
    tracker = AlertingTracker()
    # establish a stable baseline
    for _ in range(15):
        tracker.update(tss, value=20.0, rule_state="SAFE")
    # sustained mild elevation, never individually crossing into WARN
    fired = None
    for _ in range(40):
        result = tracker.update(tss, value=32.0, rule_state="SAFE")
        if result is not None:
            fired = result
            break
    assert fired is not None
    assert fired[1] == "cusum"


def test_stable_baseline_never_fires():
    reg = get_registry()
    ph = reg["ph"]
    tracker = AlertingTracker()
    fired = False
    for i in range(60):
        v = 7.5 + 0.05 * ((-1) ** i)
        result = tracker.update(ph, value=v, rule_state="SAFE")
        if result is not None:
            fired = True
    assert not fired

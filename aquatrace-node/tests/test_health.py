from core.health import ContaminantHealthTracker


def test_normal_readings_stay_ok():
    tracker = ContaminantHealthTracker("tss")
    for v in [24.0, 25.0, 23.5, 26.0, 24.8]:
        assert tracker.evaluate(v) == "OK"


def test_out_of_range_value_fails_immediately():
    tracker = ContaminantHealthTracker("ph")
    assert tracker.evaluate(20.0) == "FAILED"  # pH can't exceed 14


def test_negative_value_fails_immediately():
    tracker = ContaminantHealthTracker("tss")
    assert tracker.evaluate(-5.0) == "FAILED"


def test_stuck_value_transitions_suspect_then_failed():
    tracker = ContaminantHealthTracker("conductivity")
    states = [tracker.evaluate(600.0) for _ in range(12)]
    assert states[0] == "OK"
    assert "SUSPECT" in states
    assert states[-1] == "FAILED"


def test_implausible_jump_flagged_suspect():
    tracker = ContaminantHealthTracker("conductivity")
    for _ in range(10):
        tracker.evaluate(600.0 + (_ % 3))  # settle a stable rolling baseline
    state = tracker.evaluate(600.0 * 10)  # a spike-fault-like jump
    assert state == "SUSPECT"


def test_sustained_fault_does_not_poison_the_rolling_baseline():
    # a fault lasting longer than ROLLING_WINDOW ticks must stay flagged for its whole duration
    # -- not "heal" once enough of its own corrupted readings fill the reference buffer
    from core.health import ROLLING_WINDOW

    tracker = ContaminantHealthTracker("tss")
    for _ in range(10):
        tracker.evaluate(25.0 + (_ % 3))  # settle a stable, healthy baseline

    fault_states = [tracker.evaluate(25.0 * 8 + 125.0 + i) for i in range(ROLLING_WINDOW + 10)]  # spike-fault-like
    assert all(s != "OK" for s in fault_states), "sustained fault must stay flagged throughout, not just at onset"

    # once the true value returns to the pre-fault baseline, the gate should clear again
    recovery_states = [tracker.evaluate(25.0 + (_ % 3)) for _ in range(5)]
    assert recovery_states[-1] == "OK"


def test_moderate_pollution_event_does_not_trip_health_gate():
    # a genuine large but plausible injected event should not, by itself, look like a broken
    # sensor -- distinguishing the two is the tracing engine's job (cross-node mass balance),
    # not a single node's local plausibility gate
    tracker = ContaminantHealthTracker("conductivity")
    for _ in range(10):
        tracker.evaluate(600.0)
    state = tracker.evaluate(600.0 + 2000.0)  # a strong but not absurd pollution jump
    assert state == "OK"

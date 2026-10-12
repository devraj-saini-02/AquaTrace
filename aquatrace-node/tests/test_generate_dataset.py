from datetime import timedelta

from simulator.clock import SimClock
from simulator.world import WorldSimulator
from training.generate_dataset import _active_injection_origin


def test_active_injection_origin_matches_during_window_and_none_outside():
    clock = SimClock(time_scale=1.0)
    world = WorldSimulator(node_count=4, seed=5, clock=clock)
    node_id = world.node_ids()[0]
    t0 = clock.now()
    world.inject_event(node_id, "tss", magnitude=500.0, start_time=t0 + timedelta(seconds=60), duration_s=300, onset_sharpness=1.0)

    assert _active_injection_origin(world, node_id, "tss", t0) is None  # before the event starts
    assert _active_injection_origin(world, node_id, "tss", t0 + timedelta(seconds=120)) == node_id  # inside the window
    assert _active_injection_origin(world, node_id, "tss", t0 + timedelta(seconds=500)) is None  # after it ends


def test_active_injection_origin_is_none_for_unrelated_node_or_contaminant():
    clock = SimClock(time_scale=1.0)
    world = WorldSimulator(node_count=4, seed=5, clock=clock)
    node_ids = world.node_ids()
    t0 = clock.now()
    world.inject_event(node_ids[0], "tss", magnitude=500.0, start_time=t0 + timedelta(seconds=60), duration_s=300, onset_sharpness=1.0)

    mid = t0 + timedelta(seconds=120)
    assert _active_injection_origin(world, node_ids[1], "tss", mid) is None  # different node
    assert _active_injection_origin(world, node_ids[0], "bod", mid) is None  # different contaminant

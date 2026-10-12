from datetime import timedelta

from simulator.clock import SimClock
from simulator.world import WorldSimulator


def make_world(node_count=10, seed=42, time_scale=120.0):
    clock = SimClock(time_scale=time_scale)
    return WorldSimulator(node_count=node_count, seed=seed, clock=clock), clock


def test_dag_is_acyclic_and_connected():
    world, _ = make_world()
    ids = world.node_ids()
    assert len(ids) == 10
    # topo_order is valid: every edge's parent appears before its child
    index = {n: i for i, n in enumerate(world.topo_order)}
    for e in world.edges:
        assert index[e.parent_id] < index[e.child_id]
    # exactly one root (no parents) — the DAG grows from a single headwater
    roots = [n for n in ids if not world.parents_of[n]]
    assert roots == [ids[0]]


def test_reach_lengths_are_realistic():
    world, _ = make_world(node_count=20)
    for e in world.edges:
        assert 20.0 <= e.length_m <= 2000.0  # not the multi-km spans an earlier bug produced


def test_discharge_is_monotonically_non_decreasing_downstream():
    world, clock = make_world(node_count=15)
    t0 = clock.now()
    q = {n: world.get_hydraulics(n, t0)[2] for n in world.node_ids()}
    for e in world.edges:
        assert q[e.child_id] >= q[e.parent_id] * 0.999  # small float slack


def test_unknown_node_raises_on_injection():
    world, clock = make_world()
    t0 = clock.now()
    try:
        world.inject_event("NOT-A-REAL-NODE", "conductivity", 100.0, t0, 60.0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_injected_event_propagates_downstream_and_decays_back_to_baseline():
    world, clock = make_world(node_count=8, seed=7)
    root = world.node_ids()[0]
    target = world.node_ids()[-1]
    t0 = clock.now()

    baseline_before = world.get_reading(target, "conductivity", t0)
    world.inject_event(root, "conductivity", magnitude=3000.0, start_time=t0 + timedelta(seconds=60), duration_s=600, onset_sharpness=1.5)

    readings = []
    for i in range(500):
        t = t0 + timedelta(seconds=i * 30)
        readings.append((i * 30, world.get_reading(root, "conductivity", t), world.get_reading(target, "conductivity", t)))

    peak_root_t = max(readings, key=lambda r: r[1])[0]
    peak_target_t = max(readings, key=lambda r: r[2])[0]
    assert peak_target_t > peak_root_t  # arrives later downstream, as causality requires

    tail_target_values = [r[2] for r in readings[-10:]]
    assert max(tail_target_values) < baseline_before * 3  # decayed back toward baseline, not stuck at the peak


def test_event_has_zero_marginal_effect_before_it_starts():
    world, clock = make_world(node_count=6, seed=3)
    root = world.node_ids()[0]
    t0 = clock.now()
    before = world.get_reading(root, "conductivity", t0)
    world.inject_event(root, "conductivity", magnitude=5000.0, start_time=t0 + timedelta(hours=10), duration_s=60, onset_sharpness=1.0)
    after = world.get_reading(root, "conductivity", t0)
    assert after == before


def test_sensor_fault_stuck_holds_last_good_value():
    world, clock = make_world(node_count=5, seed=1)
    n = world.node_ids()[0]
    t0 = clock.now()
    good = world.get_reading(n, "tss", t0)
    world.inject_sensor_fault(n, "tss", "stuck", t0 + timedelta(seconds=30), 300)
    v1 = world.get_reading(n, "tss", t0 + timedelta(seconds=30))
    v2 = world.get_reading(n, "tss", t0 + timedelta(seconds=150))
    assert v1 == good
    assert v2 == good


def test_sensor_fault_dropout_reads_zero_during_window():
    world, clock = make_world(node_count=5, seed=1)
    n = world.node_ids()[0]
    t0 = clock.now()
    world.inject_sensor_fault(n, "tss", "dropout", t0 + timedelta(seconds=30), 120)
    assert world.get_reading(n, "tss", t0 + timedelta(seconds=60)) == 0.0
    recovered = world.get_reading(n, "tss", t0 + timedelta(seconds=300))
    assert recovered > 0.0


def test_sensor_fault_spike_reads_much_higher_during_window():
    world, clock = make_world(node_count=5, seed=1)
    n = world.node_ids()[0]
    t0 = clock.now()
    baseline = world.get_reading(n, "tss", t0)
    world.inject_sensor_fault(n, "tss", "spike", t0 + timedelta(seconds=30), 60)
    spiked = world.get_reading(n, "tss", t0 + timedelta(seconds=30))
    assert spiked > baseline * 3


def test_ph_and_temperature_are_not_transported():
    world, _ = make_world(node_count=6)
    assert "ph" not in world._transportable_ids()
    assert "temperature" not in world._transportable_ids()


def test_fecal_coliform_excluded_from_live_sensor_ids():
    world, _ = make_world(node_count=4)
    assert "fecal_coliform" not in world.sensor_ids_for(world.node_ids()[0])

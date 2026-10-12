from datetime import datetime, timezone

import pytest

from app.models import Edge
from app.tracing.engine import _confidence, _topological_sort, classify_cause, current_tau


def test_topological_sort_respects_edges():
    node_ids = {"A", "B", "C", "D"}
    edges = [
        Edge(parent_node_id="A", child_node_id="B", length_m=1, manning_n=0.014, slope=0.001, tau_base_s=10),
        Edge(parent_node_id="B", child_node_id="C", length_m=1, manning_n=0.014, slope=0.001, tau_base_s=10),
        Edge(parent_node_id="A", child_node_id="D", length_m=1, manning_n=0.014, slope=0.001, tau_base_s=10),
    ]
    order = _topological_sort(node_ids, edges)
    index = {n: i for i, n in enumerate(order)}
    assert index["A"] < index["B"] < index["C"]
    assert index["A"] < index["D"]


def test_topological_sort_handles_disconnected_nodes():
    node_ids = {"X", "Y"}
    order = _topological_sort(node_ids, [])
    assert set(order) == node_ids


def test_current_tau_uses_live_speed_when_available():
    edge = Edge(parent_node_id="A", child_node_id="B", length_m=500.0, manning_n=0.014, slope=0.002, tau_base_s=1000.0)
    tau = current_tau(edge, parent_latest_speed_mps=1.0)
    assert tau == pytest.approx(500.0)


def test_current_tau_falls_back_to_base_when_no_live_speed():
    edge = Edge(parent_node_id="A", child_node_id="B", length_m=500.0, manning_n=0.014, slope=0.002, tau_base_s=1000.0)
    assert current_tau(edge, None) == 1000.0
    assert current_tau(edge, 0.0) == 1000.0


def test_confidence_below_threshold_is_zero():
    assert _confidence(u=1.0, sigma_u=1.0) == 0.0  # z=1 < 2, below the trigger threshold


def test_confidence_increases_with_z_score():
    low = _confidence(u=3.0, sigma_u=1.0)
    high = _confidence(u=10.0, sigma_u=1.0)
    assert 0.0 < low < high <= 0.99


@pytest.mark.asyncio
async def test_classify_headwater_above_own_baseline_is_local_injection():
    # a headwater's own m_expected is its preceding-window baseline (see run_tracing), not 0 --
    # here it jumped to 10x that baseline, which should read as a local anomaly
    cause = await classify_cause(
        session=None, u=4500.0, m_observed=5000.0, m_expected=500.0, node_id="ROOT", parent_contribs=[], contaminant_id="tss"
    )
    assert cause["type"] == "local_injection"


@pytest.mark.asyncio
async def test_classify_headwater_matching_own_baseline_is_pass_through():
    # normal headwater fluctuation: current mass is in line with its own recent history
    cause = await classify_cause(
        session=None, u=50.0, m_observed=550.0, m_expected=500.0, node_id="ROOT", parent_contribs=[], contaminant_id="tss"
    )
    assert cause["type"] == "pass_through"


@pytest.mark.asyncio
async def test_classify_unexplained_excess_with_no_parents_signal_is_local_injection():
    edge = Edge(parent_node_id="P", child_node_id="C", length_m=100, manning_n=0.014, slope=0.002, tau_base_s=200)
    cause = await classify_cause(
        session=None,
        u=999.0,
        m_observed=1000.0,
        m_expected=1.0,  # parents explain essentially nothing
        node_id="C",
        parent_contribs=[("P", edge, 200.0, 1.0)],
        contaminant_id="tss",
    )
    assert cause["type"] == "local_injection"


@pytest.mark.asyncio
async def test_classify_partial_explanation_defaults_to_pass_through_not_new_source():
    # ambiguous middle ground (20%-60% explained): a real, far-downstream node whose signal has
    # been smeared by several hops of dispersion the simplified shift-and-decay model can't
    # fully track -- should NOT manufacture a coincidental new local source here
    edge = Edge(parent_node_id="P", child_node_id="C", length_m=700, manning_n=0.014, slope=0.002, tau_base_s=1500)
    cause = await classify_cause(
        session=None,
        u=20000.0,
        m_observed=35000.0,
        m_expected=15000.0,  # 43% explained
        node_id="C",
        parent_contribs=[("P", edge, 1500.0, 15000.0)],
        contaminant_id="tss",
    )
    assert cause["type"] == "pass_through"
    assert cause["dominant_parent_id"] == "P"

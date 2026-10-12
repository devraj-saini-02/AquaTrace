import numpy as np
import pytest

from app.physics import (
    convolve_parent_flux_to_child_concentration,
    convolve_parent_flux_to_child_instant,
    discharge,
    dispersion_coefficient,
    first_order_decay,
    gaussian_slug_concentration,
    hydraulic_radius,
    junction_expected_mass,
    manning_level_from_discharge,
    manning_velocity,
    shear_velocity,
    streeter_phelps_deficit,
    travel_time_s,
    unexplained_mass,
)


def test_discharge_continuity():
    assert discharge(speed_mps=1.0, breadth_m=2.0, level_m=0.5) == pytest.approx(1.0)


def test_hydraulic_radius_wide_channel_approaches_level():
    # for breadth >> level, R ~= level (the spec's stated wide-channel approximation)
    r = hydraulic_radius(level_m=0.2, breadth_m=1000.0)
    assert r == pytest.approx(0.2, rel=1e-2)


def test_hydraulic_radius_zero_level():
    assert hydraulic_radius(level_m=0.0, breadth_m=3.0) == 0.0


def test_manning_velocity_positive_for_valid_inputs():
    v = manning_velocity(level_m=0.3, breadth_m=3.0, manning_n=0.014, slope=0.002)
    assert v > 0


def test_manning_velocity_zero_for_invalid_inputs():
    assert manning_velocity(level_m=0.0, breadth_m=3.0, manning_n=0.014, slope=0.002) == 0.0
    assert manning_velocity(level_m=0.3, breadth_m=3.0, manning_n=0.0, slope=0.002) == 0.0


def test_travel_time_scales_inversely_with_speed():
    tau_slow = travel_time_s(length_m=100.0, speed_mps=0.5)
    tau_fast = travel_time_s(length_m=100.0, speed_mps=1.0)
    assert tau_slow == pytest.approx(200.0)
    assert tau_fast == pytest.approx(100.0)
    assert tau_fast < tau_slow


def test_manning_level_from_discharge_round_trips_through_manning_velocity():
    breadth, n, slope = 3.0, 0.014, 0.002
    level_in = 0.35
    v = manning_velocity(level_in, breadth, n, slope)
    q = discharge(v, breadth, level_in)
    level_out = manning_level_from_discharge(q, breadth, n, slope)
    assert level_out == pytest.approx(level_in, rel=1e-6)


def test_manning_level_from_discharge_zero_for_invalid_inputs():
    assert manning_level_from_discharge(0.0, 3.0, 0.014, 0.002) == 0.0
    assert manning_level_from_discharge(1.0, 3.0, 0.0, 0.002) == 0.0


def test_travel_time_infinite_for_zero_speed():
    assert travel_time_s(length_m=100.0, speed_mps=0.0) == float("inf")


def test_shear_velocity_matches_formula():
    # u* = sqrt(g*H*S)
    u_star = shear_velocity(depth_m=1.0, slope=0.01)
    assert u_star == pytest.approx(np.sqrt(9.81 * 1.0 * 0.01))


def test_dispersion_coefficient_positive():
    d = dispersion_coefficient(speed_mps=0.5, width_m=3.0, depth_m=0.3, slope=0.002)
    assert d > 0


def test_first_order_decay_conservative_species_no_decay():
    assert first_order_decay(c0=10.0, k_per_s=0.0, t_s=500.0) == pytest.approx(10.0)


def test_first_order_decay_reduces_concentration():
    c = first_order_decay(c0=10.0, k_per_s=1e-4, t_s=1000.0)
    assert 0 < c < 10.0


def test_gaussian_slug_concentration_zero_before_injection():
    c = gaussian_slug_concentration(
        mass_g=100.0, area_m2=1.0, dispersion_m2s=0.5, t_s=np.array([-1.0, 0.0]), x_m=10.0, speed_mps=0.5, k_per_s=0.0
    )
    assert np.all(c == 0.0)


def test_gaussian_slug_concentration_peaks_near_advection_time():
    # peak of the slug at x should arrive close to t = x / v
    x, v = 50.0, 0.5
    t_arr = np.linspace(1.0, 300.0, 500)
    c = gaussian_slug_concentration(
        mass_g=1000.0, area_m2=1.0, dispersion_m2s=1.0, t_s=t_arr, x_m=x, speed_mps=v, k_per_s=0.0
    )
    t_peak = t_arr[np.argmax(c)]
    assert t_peak == pytest.approx(x / v, rel=0.15)


def test_gaussian_slug_mass_conserved_without_decay():
    # integrating C(x,t)*v*A over t at a fixed x should recover ~M for k=0 (advective flux-mass check)
    x, v, A = 50.0, 0.5, 1.0
    t_arr = np.linspace(0.01, 600.0, 20000)
    dt = t_arr[1] - t_arr[0]
    c = gaussian_slug_concentration(mass_g=1000.0, area_m2=A, dispersion_m2s=1.0, t_s=t_arr, x_m=x, speed_mps=v, k_per_s=0.0)
    recovered_mass = np.sum(c * v * A) * dt
    assert recovered_mass == pytest.approx(1000.0, rel=0.1)


def test_convolution_produces_nonzero_downstream_signal():
    n = 200
    dt = 5.0
    flux = np.zeros(n)
    flux[10] = 500.0  # a pulse of mass injected upstream
    child_conc = convolve_parent_flux_to_child_concentration(
        parent_flux_g_s=flux, dt_s=dt, x_m=100.0, dispersion_m2s=2.0, speed_mps=0.5, k_per_s=0.0, area_m2=1.0
    )
    assert len(child_conc) == n
    assert np.max(child_conc) > 0
    assert np.argmax(child_conc) > 10  # peak arrives after the injection index


def test_convolve_instant_matches_full_convolution_last_element():
    rng = np.random.default_rng(7)
    flux = rng.uniform(0, 100, size=80)
    dt = 30.0
    full = convolve_parent_flux_to_child_concentration(flux, dt, 200.0, 3.0, 0.4, 1e-5, 2.0)
    instant = convolve_parent_flux_to_child_instant(flux, dt, 200.0, 3.0, 0.4, 1e-5, 2.0)
    assert instant == pytest.approx(full[-1], rel=1e-9)


def test_junction_expected_mass_sums_parents_with_decay():
    expected = junction_expected_mass(parent_masses_g=[100.0, 50.0], taus_s=[10.0, 20.0], k_per_s=0.01)
    manual = 100.0 * np.exp(-0.01 * 10.0) + 50.0 * np.exp(-0.01 * 20.0)
    assert expected == pytest.approx(manual)


def test_unexplained_mass_positive_indicates_local_injection():
    u = unexplained_mass(observed_mass_g=200.0, expected_mass_g=120.0)
    assert u == pytest.approx(80.0)


def test_streeter_phelps_deficit_returns_zero_at_t_zero_with_no_initial_deficit():
    d = streeter_phelps_deficit(
        t_s=np.array([0.0]), initial_ultimate_bod_g_m3=20.0, k_deoxygenation_per_s=1e-5,
        k_reaeration_per_s=2e-5, initial_deficit_g_m3=0.0,
    )
    assert d[0] == pytest.approx(0.0, abs=1e-6)


def test_streeter_phelps_handles_equal_rates_without_div_by_zero():
    d = streeter_phelps_deficit(
        t_s=np.array([100.0, 200.0]), initial_ultimate_bod_g_m3=20.0, k_deoxygenation_per_s=1e-5,
        k_reaeration_per_s=1e-5, initial_deficit_g_m3=1.0,
    )
    assert np.all(np.isfinite(d))

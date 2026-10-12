"""Fluid-dynamics equations shared by the World Simulator (node/) and the tracing engine's
mass-balance math (central-system/). Duplicated verbatim into node/simulator/physics.py — kept
dependency-free (numpy only) so the copy is trivial and the two services can't drift apart
silently; a change here should be applied to both copies by hand, the same way two independent
teams implementing the same spec would.

Every equation below corresponds 1:1 to a named equation in the design spec §3.
"""
from __future__ import annotations

import numpy as np

G = 9.81  # m/s^2


def hydraulic_radius(level_m: float, breadth_m: float) -> float:
    """R = A / P for a rectangular channel. Reduces to R ~= level for a wide channel
    (breadth >> level), which is the approximation the spec calls out explicitly."""
    if level_m <= 0:
        return 0.0
    area = breadth_m * level_m
    wetted_perimeter = breadth_m + 2 * level_m
    return area / wetted_perimeter


def discharge(speed_mps: float, breadth_m: float, level_m: float) -> float:
    """Continuity: Q = v * A ~= v * (breadth * level)."""
    return speed_mps * breadth_m * level_m


def manning_velocity(level_m: float, breadth_m: float, manning_n: float, slope: float) -> float:
    """Manning's equation: v = (1/n) * R^(2/3) * S^(1/2). Used to keep level/speed/geometry
    mutually consistent instead of sampling them independently."""
    if manning_n <= 0 or slope < 0 or level_m <= 0:
        return 0.0
    r = hydraulic_radius(level_m, breadth_m)
    return (1.0 / manning_n) * (r ** (2.0 / 3.0)) * (slope ** 0.5)


def manning_level_from_discharge(
    q_m3s: float, breadth_m: float, manning_n: float, slope: float, tol: float = 1e-7, max_iter: int = 60
) -> float:
    """Inverts Manning's equation by bisection (Q is monotonically increasing in level, so this
    is well-posed) to solve for the level that carries a given discharge, using the exact same
    hydraulic_radius formula manning_velocity/discharge use — not a wide-channel closed-form
    shortcut, so this round-trips exactly with manning_velocity+discharge. Used where Q is known
    first (e.g. accumulated from upstream inflows) and level/speed need to stay
    Manning-consistent with it, rather than sampling level independently and deriving Q from
    it."""
    if q_m3s <= 0 or breadth_m <= 0 or manning_n <= 0 or slope <= 0:
        return 0.0

    def q_at(level: float) -> float:
        return discharge(manning_velocity(level, breadth_m, manning_n, slope), breadth_m, level)

    lo, hi = 1e-6, 50.0
    while q_at(hi) < q_m3s and hi < 1e6:
        hi *= 2
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if q_at(mid) < q_m3s:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def travel_time_s(length_m: float, speed_mps: float) -> float:
    """tau = L / v_avg. Recomputed per event from the current v, not a fixed constant."""
    if speed_mps <= 0:
        return float("inf")
    return length_m / speed_mps


def shear_velocity(depth_m: float, slope: float, g: float = G) -> float:
    """u* = sqrt(g * H * S)."""
    return float(np.sqrt(max(g * depth_m * slope, 0.0)))


def dispersion_coefficient(speed_mps: float, width_m: float, depth_m: float, slope: float) -> float:
    """Fischer et al. estimate: D ~= 0.011 * v^2 * W^2 / (H * u*). Falls back to a small
    positive floor if u* is ~0 (near-stagnant reach) to keep the kernel below well-defined."""
    u_star = shear_velocity(depth_m, slope)
    if depth_m <= 0 or u_star <= 1e-6:
        return 0.05
    return 0.011 * (speed_mps ** 2) * (width_m ** 2) / (depth_m * u_star)


def first_order_decay(c0: float, k_per_s: float, t_s: float) -> float:
    """C(t) = C0 * exp(-k*t). k ~= 0 for conservative species."""
    return c0 * float(np.exp(-k_per_s * t_s))


def gaussian_slug_concentration(
    mass_g: float,
    area_m2: float,
    dispersion_m2s: float,
    t_s: np.ndarray | float,
    x_m: float,
    speed_mps: float,
    k_per_s: float,
) -> np.ndarray:
    """Closed-form solution of the 1D advection-dispersion-reaction equation for an
    instantaneous slug of mass M injected at x=0, t=0:

        C(x,t) = [M / (A*sqrt(4*pi*D*t))] * exp(-(x - v*t)^2 / (4*D*t)) * exp(-k*t)

    t_s may be a scalar or an array; t<=0 is defined as C=0 (mass hasn't arrived/existed yet).
    """
    t = np.atleast_1d(np.asarray(t_s, dtype=float))
    c = np.zeros_like(t)
    valid = t > 0
    if dispersion_m2s <= 0 or area_m2 <= 0:
        return c if isinstance(t_s, np.ndarray) else c[0]
    tv = t[valid]
    prefactor = mass_g / (area_m2 * np.sqrt(4 * np.pi * dispersion_m2s * tv))
    gaussian = np.exp(-((x_m - speed_mps * tv) ** 2) / (4 * dispersion_m2s * tv))
    decay = np.exp(-k_per_s * tv)
    c[valid] = prefactor * gaussian * decay
    return c if isinstance(t_s, np.ndarray) else float(c[0])


def unit_mass_response_kernel(
    tau_s: np.ndarray,
    x_m: float,
    dispersion_m2s: float,
    speed_mps: float,
    k_per_s: float,
    area_m2: float,
) -> np.ndarray:
    """h(tau) = gaussian_slug_concentration for a *unit* mass (M=1g), i.e. the impulse response
    of the reach to a 1 g/s-instant injection. Convolving a parent's mass-flux time series
    (g/s) with this kernel and multiplying by dt gives the downstream concentration
    contribution — this is the "convolution of the parent's time series" the propagation model
    (node/simulator/world.py) uses to turn an upstream signal into a downstream one."""
    return gaussian_slug_concentration(1.0, area_m2, dispersion_m2s, tau_s, x_m, speed_mps, k_per_s)


def convolve_parent_flux_to_child_concentration(
    parent_flux_g_s: np.ndarray,
    dt_s: float,
    x_m: float,
    dispersion_m2s: float,
    speed_mps: float,
    k_per_s: float,
    area_m2: float,
) -> np.ndarray:
    """Discrete convolution of a parent's mass-flux series (g/s, evenly sampled at dt_s) with
    the unit-mass response kernel, producing the child-side concentration contribution
    (g/m^3) from that single parent. Multiple parents' outputs are summed by the caller."""
    n = len(parent_flux_g_s)
    if n == 0:
        return np.zeros(0)
    tau = np.arange(1, n + 1) * dt_s  # kernel evaluated at tau=dt_s..n*dt_s (tau=0 undefined)
    kernel = unit_mass_response_kernel(tau, x_m, dispersion_m2s, speed_mps, k_per_s, area_m2)
    conv = np.convolve(parent_flux_g_s, kernel, mode="full")[:n] * dt_s
    return conv


def convolve_parent_flux_to_child_instant(
    parent_flux_g_s: np.ndarray,
    dt_s: float,
    x_m: float,
    dispersion_m2s: float,
    speed_mps: float,
    k_per_s: float,
    area_m2: float,
) -> float:
    """The current-instant equivalent of convolve_parent_flux_to_child_concentration(...)[-1],
    computed as a direct O(n) dot product against the kernel instead of a full O(n^2) array
    convolution that would then discard everything but the last element. Mathematically
    identical to the last element of the full convolution; this is the one a per-tick
    simulation loop should call (node/simulator/world.py) when only "now" is needed."""
    n = len(parent_flux_g_s)
    if n == 0:
        return 0.0
    tau = np.arange(n, 0, -1) * dt_s  # oldest sample -> tau=n*dt_s, newest sample -> tau=dt_s
    kernel = unit_mass_response_kernel(tau, x_m, dispersion_m2s, speed_mps, k_per_s, area_m2)
    return float(np.dot(np.asarray(parent_flux_g_s, dtype=float), kernel) * dt_s)


def junction_expected_mass(parent_masses_g: list[float], taus_s: list[float], k_per_s: float) -> float:
    """M_expected(child, t) = sum_parents M_parent(t - tau) * exp(-k*tau). Callers pass the
    already-time-shifted parent masses (M_parent evaluated at t - tau) alongside each parent's
    tau so the decay term can be applied per-parent (different parents can have different tau)."""
    return sum(m * float(np.exp(-k_per_s * tau)) for m, tau in zip(parent_masses_g, taus_s))


def unexplained_mass(observed_mass_g: float, expected_mass_g: float) -> float:
    """U(t) = M_observed(child, t) - M_expected(child, t)."""
    return observed_mass_g - expected_mass_g


def streeter_phelps_deficit(
    t_s: np.ndarray | float,
    initial_ultimate_bod_g_m3: float,
    k_deoxygenation_per_s: float,
    k_reaeration_per_s: float,
    initial_deficit_g_m3: float,
) -> np.ndarray:
    """Streeter-Phelps DO sag (optional/stretch, §3):

        D(t) = [kd*L0 / (kr - kd)] * [exp(-kd*t) - exp(-kr*t)] + D0*exp(-kr*t)

    D(t) is the oxygen deficit (DO_sat - DO_actual). Degenerates gracefully (falls back to a
    pure first-order decay of the initial deficit) when kr ~= kd, avoiding a divide-by-zero.
    """
    t = np.atleast_1d(np.asarray(t_s, dtype=float))
    kd, kr = k_deoxygenation_per_s, k_reaeration_per_s
    if abs(kr - kd) < 1e-12:
        d = initial_deficit_g_m3 * np.exp(-kr * t)
    else:
        d = (kd * initial_ultimate_bod_g_m3 / (kr - kd)) * (np.exp(-kd * t) - np.exp(-kr * t))
        d += initial_deficit_g_m3 * np.exp(-kr * t)
    return d if isinstance(t_s, np.ndarray) else float(d[0])

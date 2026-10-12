from registry import get_registry


def test_high_is_bad_thresholds():
    reg = get_registry()
    tss = reg["tss"]  # limit_safe=40, limit_warn=100
    assert tss.classify(10.0) == "SAFE"
    assert tss.classify(40.0) == "SAFE"
    assert tss.classify(70.0) == "WARN"
    assert tss.classify(100.0) == "WARN"
    assert tss.classify(150.0) == "RED"


def test_low_is_bad_thresholds():
    reg = get_registry()
    do = reg["dissolved_oxygen"]  # limit_safe=5.0, limit_warn=4.0 (lower is worse)
    assert do.classify(6.0) == "SAFE"
    assert do.classify(5.0) == "SAFE"
    assert do.classify(4.5) == "WARN"
    assert do.classify(4.0) == "WARN"
    assert do.classify(2.0) == "RED"


def test_band_thresholds_ph():
    reg = get_registry()
    ph = reg["ph"]  # safe [6.5,8.5], warn [5.5,9.0]
    assert ph.classify(7.0) == "SAFE"
    assert ph.classify(6.5) == "SAFE"
    assert ph.classify(8.5) == "SAFE"
    assert ph.classify(6.0) == "WARN"
    assert ph.classify(9.0) == "WARN"
    assert ph.classify(5.0) == "RED"
    assert ph.classify(10.0) == "RED"


def test_context_polarity_always_safe():
    reg = get_registry()
    temp = reg["temperature"]
    assert temp.classify(-40.0) == "SAFE"
    assert temp.classify(1000.0) == "SAFE"


def test_null_thresholds_default_safe():
    reg = get_registry()
    conductivity = reg["conductivity"]  # limit_safe/limit_warn both null in the registry
    assert conductivity.classify(50000.0) == "SAFE"


def test_conservative_species_have_zero_decay_rate():
    reg = get_registry()
    assert reg["conductivity"].decay_rate_per_s == 0.0
    assert reg["chromium_vi"].decay_rate_per_s == 0.0


def test_non_conservative_species_have_positive_decay_rate():
    reg = get_registry()
    assert reg["bod"].decay_rate_per_s > 0.0
    assert reg["tss"].decay_rate_per_s > 0.0


def test_fecal_coliform_not_live_stream():
    reg = get_registry()
    assert reg["fecal_coliform"].is_live_stream is False
    assert "fecal_coliform" not in reg.live_stream_ids()


def test_all_other_contaminants_are_live_stream():
    reg = get_registry()
    for cid in reg.all_ids():
        if cid == "fecal_coliform":
            continue
        assert reg[cid].is_live_stream is True

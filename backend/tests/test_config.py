from dataclasses import fields, replace

from app.config import DEFAULT_CONFIG, KNOWN_CONFIG_HASHES, PARAM_META, DetectionConfig, config_hash, describe_config


def test_every_parameter_is_documented():
    for f in fields(DetectionConfig):
        assert f.name in PARAM_META, f"{f.name} lacks PARAM_META (description/unit/patterns)"
    for name, meta in PARAM_META.items():
        assert meta.description and meta.unit and meta.patterns, name


def test_hash_is_reproducible_and_ignores_version_label():
    assert config_hash(DetectionConfig()) == config_hash(DetectionConfig())
    assert config_hash(replace(DEFAULT_CONFIG, version="9.9.9")) == config_hash(DEFAULT_CONFIG)


def test_parameter_change_changes_hash():
    assert config_hash(replace(DEFAULT_CONFIG, converging_cr_max=0.65)) != config_hash(DEFAULT_CONFIG)


def test_released_version_hash_is_pinned():
    """A value change without bumping the version and registering its hash fails here."""
    assert KNOWN_CONFIG_HASHES.get(DEFAULT_CONFIG.version) == config_hash(DEFAULT_CONFIG)


def test_describe_config_covers_all_fields():
    rows = describe_config(DEFAULT_CONFIG)
    assert {r["name"] for r in rows} == {f.name for f in fields(DetectionConfig)}
    assert all(r["description"] for r in rows)


def test_quality_labels_are_presentation_bands():
    cfg = DEFAULT_CONFIG
    assert cfg.quality_label(100) == "Excellent"
    assert cfg.quality_label(85) == "Excellent"
    assert cfg.quality_label(84.99) == "Strong"
    assert cfg.quality_label(50) == "Moderate"
    assert cfg.quality_label(49.9) == "Weak"


# ---------------------------------------------------------------- E1 technical configuration
from app.config import DEFAULT_TECH_CONFIG, KNOWN_TECH_CONFIG_HASHES, TECH_PARAM_META, TechnicalConfig


def test_every_technical_parameter_is_documented():
    for f in fields(TechnicalConfig):
        if f.name == "version":
            continue
        assert f.name in TECH_PARAM_META, f"{f.name} lacks TECH_PARAM_META"
        assert TECH_PARAM_META[f.name].description


def test_technical_config_hash_is_pinned_and_separate():
    assert KNOWN_TECH_CONFIG_HASHES.get(DEFAULT_TECH_CONFIG.version) == config_hash(DEFAULT_TECH_CONFIG)
    # Technical parameters never change pattern-detection hashes / detection IDs.
    assert config_hash(DEFAULT_CONFIG) == KNOWN_CONFIG_HASHES[DEFAULT_CONFIG.version]
    assert config_hash(TechnicalConfig(rsi_period=21)) != config_hash(DEFAULT_TECH_CONFIG)


def test_index_config_hash_is_pinned_and_documented():
    from app.config import DEFAULT_INDEX_CONFIG, INDEX_PARAM_META, KNOWN_INDEX_CONFIG_HASHES, IndexConfig

    assert KNOWN_INDEX_CONFIG_HASHES.get(DEFAULT_INDEX_CONFIG.version) == config_hash(DEFAULT_INDEX_CONFIG)
    for f in fields(IndexConfig):
        assert f.name == "version" or f.name in INDEX_PARAM_META, f"{f.name} lacks INDEX_PARAM_META"


def test_stage_config_hash_pinned_and_every_parameter_tagged():
    from app.config import DEFAULT_STAGE_CONFIG, KNOWN_STAGE_CONFIG_HASHES, STAGE_ASSUMPTION_PARAMS, STAGE_PARAM_META, STAGE_SOURCE_PARAMS, STAGE_USER_PARAMS, StageConfig

    assert KNOWN_STAGE_CONFIG_HASHES.get(DEFAULT_STAGE_CONFIG.version) == config_hash(DEFAULT_STAGE_CONFIG)
    names = {f.name for f in fields(StageConfig)} - {"version"}
    tags = [set(STAGE_SOURCE_PARAMS), set(STAGE_ASSUMPTION_PARAMS), set(STAGE_USER_PARAMS)]
    assert names == set().union(*tags) and sum(len(t) for t in tags) == len(names)  # every parameter has exactly one tag
    for n in names:
        tag = "ASSUMPTION" if n in STAGE_ASSUMPTION_PARAMS else "USER" if n in STAGE_USER_PARAMS else "SOURCE"
        assert STAGE_PARAM_META[n].description.startswith(tag), n


# ---------------------------------------------------------------- advanced scanners, VCP, confluence
def test_advanced_configs_pinned_documented_and_separate():
    from app.config import (
        CONFLUENCE_PARAM_META,
        DEFAULT_CONFLUENCE_CONFIG,
        DEFAULT_SIGNAL_CONFIG,
        DEFAULT_STAGE_CONFIG,
        DEFAULT_VCP_CONFIG,
        KNOWN_CONFLUENCE_CONFIG_HASHES,
        KNOWN_SIGNAL_CONFIG_HASHES,
        KNOWN_STAGE_CONFIG_HASHES,
        KNOWN_VCP_CONFIG_HASHES,
        SIGNAL_PARAM_META,
        VCP_PARAM_META,
        ConfluenceConfig,
        SignalConfig,
        VCPConfig,
    )

    for cfg, known, meta, cls in (
        (DEFAULT_SIGNAL_CONFIG, KNOWN_SIGNAL_CONFIG_HASHES, SIGNAL_PARAM_META, SignalConfig),
        (DEFAULT_VCP_CONFIG, KNOWN_VCP_CONFIG_HASHES, VCP_PARAM_META, VCPConfig),
        (DEFAULT_CONFLUENCE_CONFIG, KNOWN_CONFLUENCE_CONFIG_HASHES, CONFLUENCE_PARAM_META, ConfluenceConfig),
    ):
        assert known.get(cfg.version) == config_hash(cfg), f"{cls.__name__}: register the hash of version {cfg.version}"
        for f in fields(cls):
            assert f.name == "version" or (f.name in meta and meta[f.name].description), f"{cls.__name__}.{f.name} lacks documentation"
    # The new configurations never change pattern, technical or Stage Analysis identities.
    assert config_hash(DEFAULT_CONFIG) == KNOWN_CONFIG_HASHES[DEFAULT_CONFIG.version]
    assert config_hash(DEFAULT_TECH_CONFIG) == KNOWN_TECH_CONFIG_HASHES[DEFAULT_TECH_CONFIG.version]
    assert config_hash(DEFAULT_STAGE_CONFIG) == KNOWN_STAGE_CONFIG_HASHES[DEFAULT_STAGE_CONFIG.version]
    assert DEFAULT_SIGNAL_CONFIG.atr_period == DEFAULT_CONFIG.atr_period == DEFAULT_VCP_CONFIG.atr_period
    assert config_hash(SignalConfig(vs_rvol_threshold=3.0)) != config_hash(DEFAULT_SIGNAL_CONFIG)


def test_multi_month_timeframes_are_chart_only():
    from app.config import CHART_TIMEFRAMES, DETECTOR_TIMEFRAMES, MULTI_MONTH_TIMEFRAMES, TIMEFRAMES

    assert set(MULTI_MONTH_TIMEFRAMES) == {"1mo", "3mo", "6mo", "12mo"} and not set(MULTI_MONTH_TIMEFRAMES) & set(TIMEFRAMES)
    assert CHART_TIMEFRAMES == TIMEFRAMES + MULTI_MONTH_TIMEFRAMES
    for name, tfs in DETECTOR_TIMEFRAMES.items():
        if name != "indicators":
            assert not set(tfs) & set(MULTI_MONTH_TIMEFRAMES), f"{name} must not run on multi-month candles"

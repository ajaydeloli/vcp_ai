"""Table layouts from before the strategy dimension (main 5251638), for migration tests."""

from __future__ import annotations

OLD_DDL_SCAN_RUNS = """
CREATE TABLE IF NOT EXISTS scan_runs (
    scan_run_id          VARCHAR     PRIMARY KEY,
    scan_type            VARCHAR     NOT NULL,
    as_of_date           DATE        NOT NULL,
    scan_id              VARCHAR     NOT NULL,
    data_snapshot_id     VARCHAR     NOT NULL,
    data_cutoff          TIMESTAMPTZ NOT NULL,
    universe_snapshot_id VARCHAR     NOT NULL,
    universe_cutoff      TIMESTAMPTZ,
    scan_config_hash     VARCHAR     NOT NULL,
    section_hashes       VARCHAR     NOT NULL,
    code_commit          VARCHAR     NOT NULL,
    code_dirty           BOOLEAN,
    versions             VARCHAR     NOT NULL,
    survivorship_status  VARCHAR,
    survivorship_detail  VARCHAR,
    counts               VARCHAR     NOT NULL,
    results_hash         VARCHAR     NOT NULL,
    started_at           TIMESTAMPTZ NOT NULL,
    completed_at         TIMESTAMPTZ NOT NULL,
    status               VARCHAR     NOT NULL
)
"""

OLD_DDL_SETUP_SCORES = """
CREATE TABLE IF NOT EXISTS setup_scores (
    scan_id               VARCHAR NOT NULL,
    instrument_id         VARCHAR NOT NULL,
    as_of_date            DATE NOT NULL,
    classification        VARCHAR,
    vcp_status            VARCHAR,
    eligible              BOOLEAN NOT NULL,
    trend_score           DOUBLE,
    vcp_score             DOUBLE,
    volume_score          DOUBLE,
    rs_score              DOUBLE,
    fundamental_score     DOUBLE,
    final_setup_score     DOUBLE,
    ranking_percentile    DOUBLE,
    confirmation_state    VARCHAR,
    fundamental_available BOOLEAN NOT NULL,
    weights_renormalized  BOOLEAN NOT NULL,
    flags                 VARCHAR,
    trend_weight          DOUBLE NOT NULL,
    vcp_weight            DOUBLE NOT NULL,
    volume_weight         DOUBLE NOT NULL,
    rs_weight             DOUBLE NOT NULL,
    fundamental_weight    DOUBLE NOT NULL,
    scoring_version       VARCHAR NOT NULL,
    config_hash           VARCHAR NOT NULL,
    data_snapshot_id      VARCHAR NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scan_id, instrument_id)
)
"""

OLD_DDL_SCORE_COMPONENTS = """
CREATE TABLE IF NOT EXISTS score_components (
    scan_id                 VARCHAR NOT NULL,
    instrument_id           VARCHAR NOT NULL,
    component               VARCHAR NOT NULL,
    sub_component           VARCHAR NOT NULL,
    raw_measurement         DOUBLE,
    normalized_0_100        DOUBLE,
    weight_within_component DOUBLE NOT NULL,
    points                  DOUBLE NOT NULL,
    max_points              DOUBLE NOT NULL,
    scoring_version         VARCHAR NOT NULL,
    PRIMARY KEY (scan_id, instrument_id, component, sub_component)
)
"""

OLD_DDL_FORWARD_LABELS = """
CREATE TABLE IF NOT EXISTS forward_labels (
    instrument_id      VARCHAR NOT NULL,
    as_of_date         DATE NOT NULL,
    config_hash        VARCHAR NOT NULL,
    label_version      VARCHAR NOT NULL,
    data_snapshot_id   VARCHAR NOT NULL,
    score_scan_id      VARCHAR NOT NULL,
    entry_close        DOUBLE NOT NULL,
    pivot_price        DOUBLE,
    ret_5              DOUBLE,
    ret_10             DOUBLE,
    ret_20             DOUBLE,
    ret_40             DOUBLE,
    ret_60             DOUBLE,
    mfe_60             DOUBLE,
    mae_60             DOUBLE,
    breakout_within_20 BOOLEAN,
    breakout_day       INTEGER,
    failed_breakout    BOOLEAN,
    bars_after         INTEGER NOT NULL,
    complete           BOOLEAN NOT NULL,
    computed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, as_of_date, config_hash, label_version, data_snapshot_id)
)
"""

OLD_DDL_BACKTEST_RUNS = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_id         VARCHAR PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL,
    completed_at        TIMESTAMPTZ,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    universe_definition VARCHAR NOT NULL,
    strategy_version    VARCHAR NOT NULL,
    config_hash         VARCHAR NOT NULL,
    data_snapshot_id    VARCHAR NOT NULL,
    execution_model     VARCHAR NOT NULL,
    research_mode       BOOLEAN NOT NULL,
    survivorship_status VARCHAR,
    status              VARCHAR NOT NULL,
    period_name         VARCHAR,
    settings_json       VARCHAR NOT NULL,
    metrics_json        VARCHAR,
    code_commit         VARCHAR
)
"""

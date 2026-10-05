"""``vcp research ...``: golden dataset and blind labelling sheet (Phase 6 step 8)."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.cli_pipeline import _err, _open_store
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "vcp"


def add_research_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    research = subparsers.add_parser("research", help="Golden dataset and labelling (VCP §57)")
    sub = research.add_subparsers(dest="research_command")

    golden = sub.add_parser("golden", help="Score the detector on the golden fixtures")
    golden.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    golden.add_argument("--split", choices=["development", "holdout", "all"], default="all")
    golden.add_argument(
        "--record", action="store_true",
        help="Re-record every fixture's detector baseline (after a reviewed detector change)",
    )  # fmt: skip
    golden.add_argument("--config-dir", default="config")

    sheet = sub.add_parser("labelling-sheet", help="Write a blind labelling sheet")
    sheet.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    sheet.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    sheet.add_argument("--per-stratum", type=int, default=25)
    sheet.add_argument("--seed", type=int, default=20261002)
    sheet.add_argument("--out", required=True, help="Output folder")
    sheet.add_argument("--db", default="data/vcp_scanner.duckdb")
    sheet.add_argument("--config-dir", default="config")

    oc = sub.add_parser(
        "outcomes", help="Forward outcomes of passer windows by detector class (VCP 53, 62)"
    )
    oc.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    oc.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    oc.add_argument("--split", required=True, metavar="YYYY-MM-DD",
                    help="Last as-of date of the development period")  # fmt: skip
    oc.add_argument(
        "--variant", action="append", default=[], metavar="NAME:SECTION.KEY=VALUE[,...]",
        help="Extra config to compare on the development period, e.g. "
        "rs10:vcp.pivot.max_right_side_range_pct=10 (repeatable)",
    )  # fmt: skip
    oc.add_argument(
        "--scan-config-hash", metavar="HASH12",
        help="Use Trend Template scans made under this config hash (when only VCP research "
        "settings changed since)",
    )  # fmt: skip
    oc.add_argument(
        "--validate-rule",
        metavar="RULE",
        help="Also show validation for this exit rule (chosen on development first)",
    )
    oc.add_argument("--csv", help="Write every window (all variants) to this CSV")
    oc.add_argument("--db", default="data/vcp_scanner.duckdb")
    oc.add_argument("--config-dir", default="config")

    rv = sub.add_parser("review-sheet", help="Write a mark-check sheet (detector marks drawn)")
    rv.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    rv.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    rv.add_argument("--seed", type=int, default=20261003)
    rv.add_argument("--strategy", default="vcp",
                    help="Strategy whose stored setups to review (default: vcp)")  # fmt: skip
    rv.add_argument("--out", required=True, help="Output folder")
    rv.add_argument("--db", default="data/vcp_scanner.duckdb")
    rv.add_argument("--config-dir", default="config")

    so = sub.add_parser(
        "score-outcomes", help="Do higher setup scores lead to better outcomes? (Phase 7 step 5)"
    )
    so.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    so.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    so.add_argument("--split", required=True, metavar="YYYY-MM-DD",
                    help="Last as-of date of the development period")  # fmt: skip
    so.add_argument("--scan-config-hash", metavar="HASH12",
                    help="Use Trend Template scans made under this config hash")  # fmt: skip
    so.add_argument("--db", default="data/vcp_scanner.duckdb")
    so.add_argument("--config-dir", default="config")

    imp = sub.add_parser("import-labels", help="Turn the sheet's CSV into golden fixtures")
    imp.add_argument("--candidates", required=True, help="candidates.json from the sheet folder")
    imp.add_argument("--labels", required=True, help="The downloaded vcp_labels.csv")
    imp.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))


def run_research(args: argparse.Namespace) -> int:
    cmd = getattr(args, "research_command", None)
    if cmd == "golden":
        return _golden(args)
    if cmd == "labelling-sheet":
        return _sheet(args)
    if cmd == "import-labels":
        return _import(args)
    if cmd == "outcomes":
        return _outcomes(args)
    if cmd == "review-sheet":
        return _review(args)
    if cmd == "score-outcomes":
        return _score_outcomes(args)
    _err(
        "Usage: vcp research {golden,labelling-sheet,review-sheet,import-labels,outcomes,"
        "score-outcomes} ..."
    )
    return 1


def _golden(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from vcp_scanner.research.golden import (
        detector_answer,
        evaluate,
        format_report,
        load_fixtures,
        run_fixture,
        write_fixture,
    )
    from vcp_scanner.versioning import VCP_ALGORITHM_VERSION

    cfg = load_scanner_config(args.config_dir).strategy
    root = Path(args.fixtures)
    fixtures = load_fixtures(root) if root.exists() else []
    if not fixtures:
        print(f"No golden fixtures under {root} yet (label a sheet and import it first).")
        return 0
    if args.record:
        for fx in fixtures:
            answer = detector_answer(run_fixture(fx, cfg.vcp, cfg.classification),
                                     VCP_ALGORITHM_VERSION)  # fmt: skip
            write_fixture(replace(fx, detector_baseline=answer), root)
        print(f"Re-recorded detector baselines for {len(fixtures)} fixtures.")
    split = None if args.split == "all" else args.split
    print(format_report(evaluate(fixtures, cfg.vcp, cfg.classification, split)))
    return 0


def _sheet(args: argparse.Namespace) -> int:
    from vcp_scanner.research.labelling import collect_candidates, sample, write_outputs

    cfg = load_scanner_config(args.config_dir)
    config_hash = scan_config_hash(cfg)
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    with _open_store(args.db) as store:
        dates = [
            r[0]
            for r in store.conn.execute(
                "SELECT DISTINCT as_of_date FROM trend_template_results WHERE scan_id LIKE ?"
                " AND as_of_date BETWEEN ? AND ? ORDER BY 1",
                [f"trend-%-{config_hash[:12]}", start, end],
            ).fetchall()
        ]
        if not dates:
            _err(f"No Trend Template scans with config {config_hash[:12]} between {start} and "
                 f"{end}. Run universe / rs / trend-template for those dates first.")  # fmt: skip
            return 1
        candidates = collect_candidates(store, dates, config_hash, cfg.strategy.vcp,
                                        cfg.strategy.classification)  # fmt: skip
    chosen = sample(candidates, args.per_stratum, args.seed)
    title = f"VCP labelling {start}..{end}"
    paths = write_outputs(chosen, Path(args.out), title=title)
    from collections import Counter

    print(f"Scan dates  : {len(dates)} ({dates[0]} .. {dates[-1]})")
    print(f"Candidates  : {len(candidates)} Trend Template passers with weekly Stage 2")
    print(f"Sheet       : {len(chosen)} windows -> {paths['sheet']}")
    print(f"Strata      : {dict(sorted(Counter(c.stratum for c in chosen).items()))} (hidden)")
    print(f"Key (hidden): {paths['key']}  -- do not open until labelling is finished")
    return 0


def _review(args: argparse.Namespace) -> int:
    from collections import Counter

    if getattr(args, "strategy", "vcp") != "vcp":
        return _strategy_review(args)
    from vcp_scanner.research.labelling import collect_candidates
    from vcp_scanner.research.review import build_windows, select, write_review

    cfg = load_scanner_config(args.config_dir)
    config_hash = scan_config_hash(cfg)
    vcp, cls = cfg.strategy.vcp, cfg.strategy.classification
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    with _open_store(args.db) as store:
        dates = [
            r[0]
            for r in store.conn.execute(
                "SELECT DISTINCT as_of_date FROM trend_template_results WHERE scan_id LIKE ?"
                " AND as_of_date BETWEEN ? AND ? ORDER BY 1",
                [f"trend-%-{config_hash[:12]}", start, end],
            ).fetchall()
        ]
        if not dates:
            _err(f"No Trend Template scans with config {config_hash[:12]} between {start} and "
                 f"{end}.")  # fmt: skip
            return 1
        candidates = collect_candidates(store, dates, config_hash, vcp, cls)
    chosen = select(candidates, args.seed)
    windows = build_windows(chosen, vcp, cls, config_hash)
    paths = write_review(windows, Path(args.out), title=f"VCP mark check {start}..{end}")
    print(f"Scan dates : {len(dates)} ({dates[0]} .. {dates[-1]})")
    print(f"Sheet      : {len(windows)} windows -> {paths['sheet']}")
    print(f"Strata     : {dict(sorted(Counter(c.stratum for c in chosen).items()))}")
    return 0


def _strategy_review(args: argparse.Namespace) -> int:
    """Chart review of a file-configured strategy's stored setups (decision D6, C1)."""
    from collections import Counter

    from vcp_scanner.config.strategies import strategy_config_hash
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research import strategy_review as sr

    cfg = load_scanner_config(args.config_dir)
    try:
        rt = load_runtime(args.config_dir, args.strategy)
    except Exception as e:
        _err(f"Strategy error: {e}")
        return 1
    config_hash = strategy_config_hash(cfg, rt.file)
    grade2 = next((t for t in rt.tiers if rt.grade(t) == 2), "")
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    with _open_store(args.db) as store:
        setups = sr.load_setups(store, rt.strategy_id, config_hash, start, end)
        if not setups:
            _err(f"No stored {rt.strategy_id} setups with config {config_hash[:12]} between "
                 f"{start} and {end}. Run `vcp compute setups --strategy {rt.strategy_id}` "
                 "for those dates first.")  # fmt: skip
            return 1
        chosen = sr.select(setups, grade2, args.seed)
        windows = sr.build_windows(store, chosen, grade2, rt.strategy_id)
    name = rt.strategy_id.replace("_", " ")
    paths = sr.write(windows, Path(args.out), f"{name} chart review {start}..{end}", name)
    print(f"Setups     : {len(setups)} stored ({start} .. {end})")
    print(f"Sheet      : {len(windows)} windows -> {paths['sheet']}")
    print(f"Strata     : {dict(sorted(Counter(w['stratum'] for w in windows).items()))}")
    return 0


def _import(args: argparse.Namespace) -> int:
    from vcp_scanner.research.labelling import import_labels

    try:
        written = import_labels(Path(args.candidates), Path(args.labels), Path(args.fixtures))
    except (ValueError, KeyError) as e:
        _err(f"Error: {e}")
        return 1
    print(f"Wrote {len(written)} fixtures under {args.fixtures}.")
    print("Next: `vcp research golden --record` to record detector baselines, then commit them.")
    return 0


def _apply_variant(strategy: Any, spec: str) -> tuple[str, Any, Any]:
    """``name:vcp.pivot.max_right_side_range_pct=10,classification.vcp.require_tight_pivot=true``"""
    name, _, assignments = spec.partition(":")
    data = strategy.model_dump()
    for item in filter(None, assignments.split(",")):
        dotted, _, raw = item.partition("=")
        *path, leaf = dotted.strip().split(".")
        node = data
        for part in path:
            node = node[part]
        old = node[leaf]
        node[leaf] = (raw.lower() == "true") if isinstance(old, bool) else type(old)(raw)
    from vcp_scanner.config.models import StrategyConfig

    s = StrategyConfig(**data)
    return name, s.vcp, s.classification


def _outcomes(args: argparse.Namespace) -> int:
    import csv

    from vcp_scanner.research.outcomes import (
        DEFAULT_RULE,
        TRADE_RULES,
        collect_windows,
        format_rules,
        format_stats,
        summarize,
    )

    cfg = load_scanner_config(args.config_dir)
    config_hash = args.scan_config_hash or scan_config_hash(cfg)
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    split = date.fromisoformat(args.split)
    variants: dict[str, tuple[Any, Any]] = {
        "current": (cfg.strategy.vcp, cfg.strategy.classification)
    }
    for spec in args.variant:
        name, v, c = _apply_variant(cfg.strategy, spec)
        variants[name] = (v, c)
    with _open_store(args.db) as store:
        dates = [r[0] for r in store.conn.execute(
            "SELECT DISTINCT as_of_date FROM trend_template_results WHERE scan_id LIKE ?"
            " AND as_of_date BETWEEN ? AND ? ORDER BY 1",
            [f"trend-%-{config_hash[:12]}", start, end]).fetchall()]  # fmt: skip
        if not dates:
            _err(f"No Trend Template scans with config {config_hash[:12]} in range.")
            return 1
        rows = collect_windows(store, dates, config_hash, variants)
    print(f"Scan dates: {len(dates)} ({dates[0]} .. {dates[-1]}); development <= {split}")
    for name, rs in rows.items():
        dev = [r for r in rs if r.as_of <= split]
        stats, cens = summarize(dev)
        print("\n" + format_stats(stats, f"[{name}] development ({cens} censored)"))
    cur = rows["current"]
    status_stats, _ = summarize(
        [r for r in cur if r.as_of <= split and r.group in ("VCP", "A_PLUS_VCP", "NEAR_A_PLUS")],
        key="status",
    )
    print("\n" + format_stats(status_stats, "[current] development, VCP/A+/near-A+ by status"))
    print("\n" + format_rules([r for r in cur if r.as_of <= split],
                              "[current] development, breakout trade by exit rule"))  # fmt: skip
    print(
        f"  (validation shows only the default rule {DEFAULT_RULE}; choose a rule on "
        "development first, then look at it once on validation)"
    )
    val, cens = summarize([r for r in cur if r.as_of > split])
    print("\n" + format_stats(val, f"[current] VALIDATION ({cens} censored)"))
    if args.validate_rule:
        chosen = [r for r in TRADE_RULES if r.name == args.validate_rule]
        if not chosen:
            _err(f"Unknown rule {args.validate_rule}; rules: "
                 + ", ".join(r.name for r in TRADE_RULES))  # fmt: skip
            return 1
        print(
            "\n"
            + format_rules(
                [r for r in cur if r.as_of > split],
                f"[current] VALIDATION, rule {chosen[0].name}",
                chosen,
            )
        )
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            header = ["variant", "as_of", "instrument_id", "symbol", "group", "status", "result",
                      "ret_20", "ret_60", "mfe_60", "mae_60", "breakout_20",
                      *(f"trade_{t.name}" for t in TRADE_RULES)]  # fmt: skip
            w.writerow(header)
            for name, rs in rows.items():
                for r in rs:
                    o = r.outcome
                    trades = {} if o is None else o.trades
                    w.writerow([name, r.as_of, r.instrument_id, r.symbol, r.group, r.status,
                                *(("",) * 6 if o is None else (o.result, round(o.ret_20, 2),
                                  round(o.ret_60, 2), round(o.mfe_60, 2), round(o.mae_60, 2),
                                  o.breakout_20)),
                                *(round(trades[t.name], 2) if t.name in trades else ""
                                  for t in TRADE_RULES)])  # fmt: skip
    return 0


def _score_outcomes(args: argparse.Namespace) -> int:
    from vcp_scanner.research.outcomes import collect_windows
    from vcp_scanner.research.score_study import format_study, standard_keys

    cfg = load_scanner_config(args.config_dir)
    config_hash = args.scan_config_hash or scan_config_hash(cfg)
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    split = date.fromisoformat(args.split)
    with _open_store(args.db) as store:
        dates = [r[0] for r in store.conn.execute(
            "SELECT DISTINCT as_of_date FROM trend_template_results WHERE scan_id LIKE ?"
            " AND as_of_date BETWEEN ? AND ? ORDER BY 1",
            [f"trend-%-{config_hash[:12]}", start, end]).fetchall()]  # fmt: skip
        if not dates:
            _err(f"No Trend Template scans with config {config_hash[:12]} in range.")
            return 1
        rows = collect_windows(
            store, dates, config_hash,
            {"current": (cfg.strategy.vcp, cfg.strategy.classification)},
            scoring=(cfg.strategy.scoring, float(cfg.strategy.trend_template.min_rs_rank)),
        )["current"]  # fmt: skip
    dev = [r for r in rows if r.as_of <= split]
    val = [r for r in rows if r.as_of > split]
    final: list[Any] = standard_keys()[:1]
    print(f"Scan dates: {len(dates)} ({dates[0]} .. {dates[-1]}); development <= {split}")
    print(f"Scores: {cfg.strategy.scoring.version}; quintiles within each date, Q5 = highest; "
          "trades = breakout trades, default exit rule")  # fmt: skip
    print("\n" + format_study(dev, standard_keys(), "[development] all Trend Template passers"))
    print(
        "\n"
        + format_study(
            [r for r in dev if r.eligible],
            final,
            "[development] ranked setups only (VCP_LIKE+, live status)",
        )
    )
    print("\n" + format_study(val, final, "[VALIDATION] all passers, final score only"))
    print(
        "\n"
        + format_study(
            [r for r in val if r.eligible],
            final,
            "[VALIDATION] ranked setups only, final score only",
        )
    )
    return 0

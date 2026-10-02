"""``vcp research ...``: golden dataset and blind labelling sheet (Phase 6 step 8)."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

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
    _err("Usage: vcp research {golden,labelling-sheet,import-labels} ...")
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

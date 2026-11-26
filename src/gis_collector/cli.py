"""Command line interface: ``gis-collector plan|collect``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from gis_collector.collector import GisCollector
from gis_collector.exceptions import GisCollectorError
from gis_collector.logging_config import configure_logging
from gis_collector.models import Feature
from gis_collector.settings import Settings
from gis_collector.spec_loader import SpecRepository

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_PARTIAL, EXIT_FATAL = 0, 3, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gis-collector", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--specs-dir", type=Path, default=Path("specs"))
    common.add_argument(
        "--source",
        action="append",
        dest="include",
        metavar="SOURCE_ID",
        help="collect only this source (repeatable)",
    )
    common.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="SOURCE_ID",
        help="skip this source (repeatable)",
    )
    common.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    common.add_argument("--log-json", action="store_true", help="JSON log lines on stderr")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", parents=[common], help="show which strategy handles each endpoint")

    run = sub.add_parser("collect", parents=[common], help="download and parse the sources")
    run.add_argument("-o", "--output", default="-", help="output file, '-' for stdout")
    run.add_argument(
        "--format",
        choices=["json", "jsonl", "geojson"],
        default="json",
        help="json: list of {source_id, geometry, properties} (default)",
    )
    run.add_argument("--indent", type=int, default=None)
    run.add_argument("--workers", type=int, help="parallel requests per endpoint")
    run.add_argument("--strict", action="store_true", help="abort on the first failed endpoint")
    run.add_argument("--no-meta", action="store_true", help="omit properties._meta")
    run.add_argument("--boundary-file", type=Path, help="GeoJSON with the region polygon")
    run.add_argument("--dream-limit", type=int, help="fetch at most N DREAM detail records")
    run.add_argument(
        "--enrich-limit",
        type=int,
        default=0,
        help="look up up to N cadastral numbers found in tiles via REST",
    )
    run.add_argument(
        "--template-values",
        type=Path,
        help='JSON file {"source_id": [{"cadnum": "..."}]} for {placeholder} endpoints',
    )
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if args.workers:
        overrides["max_workers"] = args.workers
    if args.strict:
        overrides["strict"] = True
    if args.no_meta:
        overrides["include_meta"] = False
    if args.boundary_file:
        overrides["boundary_file"] = args.boundary_file
    if args.dream_limit is not None:
        overrides["dream_max_items"] = args.dream_limit
    if args.enrich_limit:
        overrides["template_enrich_limit"] = args.enrich_limit
    if args.template_values:
        overrides["template_values"] = json.loads(args.template_values.read_text(encoding="utf-8"))
    return Settings(**overrides)


def write_features(
    features: Sequence[Feature], fmt: str, stream: TextIO, indent: int | None
) -> None:
    rows = [f.model_dump() for f in features]
    if fmt == "jsonl":
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif fmt == "geojson":
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": row["geometry"],
                    "properties": {"source_id": row["source_id"], **row["properties"]},
                }
                for row in rows
            ],
        }
        json.dump(collection, stream, ensure_ascii=False, indent=indent)
    else:
        json.dump(rows, stream, ensure_ascii=False, indent=indent)
    stream.write("\n" if fmt != "jsonl" else "")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, json_format=args.log_json)
    try:
        specs = SpecRepository(args.specs_dir).load_all()
        if args.command == "plan":
            with GisCollector(Settings()) as collector:
                for row in collector.plan(specs, include=args.include, exclude=args.exclude):
                    note = row.skip_reason or row.problem or ""
                    print(f"{row.source_id:28} {row.strategy or '-':20} {row.endpoint}  {note}")
            return EXIT_OK

        with GisCollector(_settings_from_args(args)) as collector:
            report = collector.run(specs, include=args.include, exclude=args.exclude)
        if args.output == "-":
            write_features(report.features, args.format, sys.stdout, args.indent)
        else:
            with Path(args.output).open("w", encoding="utf-8") as handle:
                write_features(report.features, args.format, handle, args.indent)
        for entry in report.endpoints:
            if entry.status == "failed":
                logger.error("FAILED %s %s: %s", entry.source_id, entry.endpoint, entry.detail)
        return EXIT_OK if report.ok else EXIT_PARTIAL
    except (GisCollectorError, OSError, ValueError) as exc:
        logger.error("%s", exc)
        return EXIT_FATAL


if __name__ == "__main__":
    raise SystemExit(main())

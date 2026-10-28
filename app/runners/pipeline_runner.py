from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.database.engine import DatabaseNotConfigured, make_engine
from app.exporters.files import export
from app.logging_config import configure_logging, get_logger
from app.reports.quality_report import write_report
from app.runners.process_runner import ProcessResult, process, read_manifest
from app.scrapers.overpass.substation_scraper import manifest_for_files, run_fetch


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


class PipelineRunner:
    def __init__(self, settings: Settings | None = None):
        self._overrides: dict = {}
        self._settings = settings

    # ------------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings(**self._overrides)
        return self._settings

    def _latest_run_id(self) -> str:
        runs = sorted(p.name for p in self.settings.raw_dir.glob("*") if (p / "manifest.json").exists())
        if not runs:
            raise SystemExit(f"No fetched runs in {self.settings.raw_dir}. Run `fetch` first.")
        return runs[-1]

    # ------------------------------------------------------------------
    def fetch(self, run_id: str | None = None) -> str:
        run_id = run_id or new_run_id()
        summary = run_fetch(self.settings, run_id)
        if summary.status == "failed":
            raise SystemExit(f"fetch failed for every tile (run {run_id}); see manifest in {summary.raw_dir}")
        return run_id

    def process(self, run_id: str | None = None, raw_files: list[str] | None = None) -> ProcessResult:
        if raw_files:
            run_id = run_id or f"file-{Path(raw_files[0]).stem}"
            manifest, raw_dir = manifest_for_files([Path(f) for f in raw_files], run_id), None
        else:
            run_id = run_id or self._latest_run_id()
            raw_dir = self.settings.raw_dir / run_id
            manifest = {**read_manifest(raw_dir), "run_id": run_id}
        result = process(manifest, raw_dir, self.settings)
        paths = export(result, self.settings.processed_dir / run_id)
        md, _ = write_report(result, self.settings.report_dir)
        log = get_logger("pipeline", run_id=run_id)
        log.info(f"outputs: {paths['substations_csv'].parent} ; report: {md}")
        return result

    def load(self, run_id: str | None = None, raw_files: list[str] | None = None) -> dict:
        from app.loaders.staging_loader import load, record_failed_run
        from app.reports.quality_report import build_summary

        result = self.process(run_id, raw_files)
        engine = make_engine(self.settings)
        try:
            summary = build_summary(result)
            load_stats = load(result, self.settings, engine, {
                "capacity": summary["capacity_grid_candidates"],
                "evidence_violations": len(summary["evidence_violations"]),
            })
        except Exception as exc:
            try:
                record_failed_run(self.settings, engine, result.run_id, repr(exc))
            except Exception as record_exc:  # database unreachable: keep the original error
                get_logger("pipeline", run_id=result.run_id).error(f"could not record failed run: {record_exc}")
            raise
        write_report(result, self.settings.report_dir, load_stats)
        return load_stats

    def run_all(self) -> None:
        run_id = self.fetch()
        if self.settings.database_url:
            self.load(run_id)
        else:
            get_logger("pipeline", run_id=run_id).warning(
                "IRIS_DATABASE_URL not set - skipping load, files and report were written")
            self.process(run_id)

    def init_db(self) -> None:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(cfg, "head")

    # ------------------------------------------------------------------
    def run(self, argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="IRIS-GRID-SUBSTATION pipeline (Task 098)")
        parser.add_argument("command", choices=["fetch", "process", "load", "all", "init-db"])
        parser.add_argument("--run-id", help="existing run to (re)process / load")
        parser.add_argument("--raw-file", nargs="+", dest="raw_files",
                            help="process Overpass JSON file(s) saved by hand (e.g. overpass-turbo export)")
        parser.add_argument("--bbox", help="south,west,north,east (default: NRW)")
        parser.add_argument("--region", help="ISO 3166-2 code, '' to disable (default: DE-NW)")
        parser.add_argument("--tile-deg", type=float, help="tile size in degrees (default: 1.0)")
        args = parser.parse_args(argv)

        if args.bbox:
            self._overrides["bbox"] = args.bbox
        if args.region is not None:
            self._overrides["region_iso"] = args.region
        if args.tile_deg:
            self._overrides["tile_deg"] = args.tile_deg
        self._settings = None
        configure_logging(self.settings.log_level, self.settings.log_json)

        try:
            if args.command == "fetch":
                print(self.fetch(args.run_id))
            elif args.command == "process":
                self.process(args.run_id, args.raw_files)
            elif args.command == "load":
                self.load(args.run_id, args.raw_files)
            elif args.command == "all":
                self.run_all()
            elif args.command == "init-db":
                self.init_db()
        except DatabaseNotConfigured as exc:
            get_logger("pipeline").error(str(exc))
            return 2
        return 0


def main() -> None:
    sys.exit(PipelineRunner().run())

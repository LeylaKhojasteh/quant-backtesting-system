"""Publish an archived run to the frontend public artifacts.

Reference: ``ARCHITECTURE.md`` Section 6 (Report Archive System) and the
frontend adapter boundary.

Flow (no API server, no database, no watcher)::

    runs/<run_id>/result.json
        |
        v  validate the BacktestResult contract
    Quant-Backtest-Lab/public/runs-<run_id>-result.json   (byte-for-byte copy)
        |
        v  add/update one entry (never duplicated)
    Quant-Backtest-Lab/public/adapters-run-manifest.json
        |
        v  the existing adapter (app/python-result.ts) resolves
           ``runs/<run_id>/result.json`` -> ``/runs-<run_id>-result.json``

The frontend still never reads Python source: it only consumes the published
JSON artifact and manifest. The original run archive is never mutated.

Usage::

    python -m src.publish_run run_0001
    python -m src.publish_run run_0001 --runs runs --public Quant-Backtest-Lab/public
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.result_contract import BacktestResult  # noqa: E402

DEFAULT_RUNS = "runs"
DEFAULT_PUBLIC = "Quant-Backtest-Lab/public"
MANIFEST_NAME = "adapters-run-manifest.json"


def public_result_name(run_id: str) -> str:
    """Published filename the adapter resolves ``runs/<id>/result.json`` to."""
    return f"runs-{run_id}-result.json"


def _validate_archive(result_path: Path) -> dict:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{result_path} is not a JSON object")
    if not payload.get("run_id"):
        raise ValueError(f"{result_path} has no run_id")
    try:
        result = BacktestResult.from_dict(payload)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{result_path} is missing required contract fields: {exc}") from exc
    errors = result.validate()
    if errors:
        raise ValueError(f"{result_path} failed contract validation: {'; '.join(errors)}")
    return payload


def _manifest_entry(payload: dict) -> dict:
    run_id = str(payload["run_id"])
    strategy = payload.get("strategy", {})
    config = payload.get("config", {})
    start = str(config.get("start") or "")
    date = start[:10] if len(start) >= 10 else start
    label = (
        f"{run_id} · {strategy.get('name', 'Strategy')} "
        f"{strategy.get('version', '')} · {config.get('symbol', '')} {date}"
    ).strip()
    description = (
        f"{strategy.get('name', 'Strategy')} on {config.get('symbol', '')} "
        f"{config.get('timeframe', '')} archived run."
    )
    return {
        "runId": run_id,
        "file": f"runs/{run_id}/result.json",
        "label": label,
        "description": description,
    }


def _load_manifest(path: Path) -> dict:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
        data.setdefault("version", 1)
        if not isinstance(data.get("runs"), list):
            data["runs"] = []
        return data
    return {"version": 1, "runs": []}


def publish_run(
    run_id: str,
    *,
    runs: str | Path = DEFAULT_RUNS,
    public: str | Path = DEFAULT_PUBLIC,
    manifest: str | Path | None = None,
) -> dict:
    """Publish one validated archived run. Returns a result summary.

    Idempotent: identical re-publishes rewrite nothing and never duplicate the
    manifest entry. Raises before writing anything when the archive is missing
    or invalid, so an incomplete run is never published.
    """
    runs_dir = Path(runs)
    result_path = runs_dir / run_id / "result.json"
    if not result_path.is_file():
        raise FileNotFoundError(f"no archived run at {result_path}")

    payload = _validate_archive(result_path)
    if str(payload["run_id"]) != run_id:
        raise ValueError(
            f"archive run_id {payload['run_id']!r} does not match directory {run_id!r}"
        )

    public_dir = Path(public)
    public_dir.mkdir(parents=True, exist_ok=True)
    dest = public_dir / public_result_name(run_id)

    source_bytes = result_path.read_bytes()
    wrote_result = (not dest.is_file()) or dest.read_bytes() != source_bytes
    if wrote_result:
        # Byte-for-byte copy: the published artifact equals the archive result.
        dest.write_bytes(source_bytes)

    manifest_path = Path(manifest) if manifest is not None else public_dir / MANIFEST_NAME
    data = _load_manifest(manifest_path)
    entries = data["runs"]
    entry = _manifest_entry(payload)
    existing = next((i for i, r in enumerate(entries) if r.get("runId") == run_id), None)
    if existing is None:
        entries.append(entry)
    else:
        entries[existing] = entry
    data["runs"] = sorted(entries, key=lambda r: str(r.get("runId", "")))

    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    old_text = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
    wrote_manifest = old_text != new_text
    if wrote_manifest:
        manifest_path.write_text(new_text, encoding="utf-8")

    return {
        "run_id": run_id,
        "archive": str(result_path),
        "published": str(dest),
        "manifest": str(manifest_path),
        "wrote_result": wrote_result,
        "wrote_manifest": wrote_manifest,
        "entry": entry,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish an archived run to the frontend public artifacts."
    )
    parser.add_argument("run_id")
    parser.add_argument("--runs", default=DEFAULT_RUNS)
    parser.add_argument("--public", default=DEFAULT_PUBLIC)
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args(argv)
    info = publish_run(
        args.run_id, runs=args.runs, public=args.public, manifest=args.manifest
    )
    print(json.dumps(info, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

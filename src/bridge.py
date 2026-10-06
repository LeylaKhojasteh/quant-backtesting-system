"""Local HTTP bridge between the frontend and the Python research workflow.

Reference: ``ARCHITECTURE.md`` Core Architecture. The bridge is orchestration
only — it converts an HTTP/JSON payload into the existing
``BacktestRunRequest``, calls the shared :func:`src.run_backtest.execute_run`,
and returns a compact JSON response. It contains no strategy, execution or
metric logic and never shells out to the CLI.

Endpoints::

    GET  /health                -> {"status": "ok", "service": ..., "version": ...}
    GET  /strategies            -> trusted strategy catalog (metadata + parameters)
    GET  /datasets              -> approved local market-data catalog (metadata only)
    GET  /runs                  -> Run Library summaries (published + local)
    GET  /runs/<run_id>         -> run detail + archived configuration
    GET  /runs/<run_id>/report  -> the run's standalone report.html
    GET  /optimizations         -> optimization study history
    GET  /optimizations/<id>    -> a saved study (ranked results)
    POST /runs                  -> execute one BacktestRunRequest, archive (+report/publish)
    POST /runs/<run_id>/publish -> publish an existing archived run (idempotent)
    POST /comparisons           -> compare 2..10 archived runs
    POST /optimizations         -> run a deterministic grid study (synchronous)
    POST /strategies/import     -> validate + store one local .py strategy
    DELETE /runs/<run_id>       -> delete a local run (+ public artifact/manifest)
    DELETE /strategies/<name>   -> delete an imported strategy file

Local-only by design: binds to 127.0.0.1 by default, no authentication, no
arbitrary command execution, no filesystem browsing endpoint, no database. Uses
only the Python standard library (no web framework required).

Run it::

    python -m src.bridge --port 8765
    python -m src.bridge --strategy-dir tests/fixtures/strategies
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import run_errors  # noqa: E402
from src import optimization, run_backtest, run_comparison, run_library, run_request  # noqa: E402
from src import strategy_import, strategy_library, strategy_loader  # noqa: E402

SERVICE = "quant-backtest-bridge"
VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STRATEGY_DIR = "strategies"
DEFAULT_DATA_DIR = "data"
MAX_BODY_BYTES = 1 << 20  # 1 MiB is plenty for a run request

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
# e.g. XAUUSD_DUKAS_2025_01_ticks.csv -> symbol XAUUSD, month 2025-01
_DATA_NAME_RE = re.compile(r"^(?P<symbol>[A-Za-z0-9]+?)_(?P<rest>.*)\.csv$", re.IGNORECASE)
_DATA_MONTH_RE = re.compile(r"(?P<year>20\d{2})[_-](?P<month>0[1-9]|1[0-2])")


def list_strategies(strategy_dir: str | Path = DEFAULT_STRATEGY_DIR) -> list[dict]:
    """Strategy catalog (built-ins + approved imported files)."""
    return strategy_library.list_strategies(strategy_dir)


def _dataset_entry(path: Path) -> dict:
    """Lightweight dataset metadata: filename + cheap stat, never a file scan."""
    match = _DATA_NAME_RE.match(path.name)
    symbol = match.group("symbol").upper() if match else path.stem.split("_")[0].upper()
    rest = (match.group("rest") if match else path.stem).upper()
    start = end = None
    month = _DATA_MONTH_RE.search(path.stem)
    if month:
        year, mon = int(month.group("year")), int(month.group("month"))
        last = calendar.monthrange(year, mon)[1]
        start = f"{year:04d}-{mon:02d}-01"
        end = f"{year:04d}-{mon:02d}-{last:02d}"
    elif "ALL" not in rest:
        # No deterministic range from the filename; leave it unavailable.
        start = end = None
    stat = path.stat()
    return {
        "id": path.name,
        "filename": path.name,
        "path": str(path),
        "symbol": symbol,
        "format": path.suffix.lstrip(".").lower(),
        "start": start,
        "end": end,
        "bytes": stat.st_size,
    }


def list_datasets(data_dir: str | Path = DEFAULT_DATA_DIR) -> list[dict]:
    """Deterministic catalog of ``*.csv`` files in the approved data directory.

    Only the configured directory is inspected (no recursion, no browsing).
    Date ranges come from the filename, never from reading the file.
    """
    base = Path(data_dir)
    if not base.is_dir():
        return []
    return [_dataset_entry(p) for p in sorted(base.glob("*.csv")) if p.is_file()]


def _error_for(exc: Exception) -> tuple[int, str, str]:
    """Map an exception to ``(http_status, code, message)`` without tracebacks."""
    if isinstance(exc, run_errors.RunError):
        return exc.http_status, exc.code, exc.message
    if isinstance(exc, strategy_import.StrategyImportError):
        return exc.http_status, exc.code, exc.message
    if isinstance(exc, strategy_loader.StrategyLoadError):
        message = str(exc)
        if "not found" in message:
            return 404, "unknown_strategy", message
        return 400, "invalid_strategy", message
    if isinstance(exc, FileNotFoundError):
        return 400, "invalid_dataset", str(exc)
    if isinstance(exc, json.JSONDecodeError):
        return 400, "invalid_request", f"invalid JSON body: {exc}"
    return 500, "internal_error", "unexpected bridge error"


def import_strategy_payload(payload, strategy_dir: str | Path) -> dict:
    """Validate/import a strategy from an HTTP payload; return a catalog entry."""
    if not isinstance(payload, dict):
        raise strategy_import.InvalidStrategyFileError("request body must be a JSON object")
    unknown = sorted(set(payload) - {"filename", "source"})
    if unknown:
        raise strategy_import.InvalidStrategyFileError(
            f"unknown request field(s): {unknown}"
        )
    strategy, target = strategy_import.import_strategy(
        payload.get("filename"), payload.get("source"), strategy_dir=strategy_dir
    )
    return strategy_library.catalog_entry(str(target), strategy, source="file", file=str(target))


def _compact_run_response(info: dict) -> dict:
    """Compact run response: references + summary, not the full result payload."""
    return {
        "run_id": info["run_id"],
        "status": info["status"],
        "strategy": info["strategy"],
        "parameters": info["parameters"],
        "market": info["market"],
        "execution": info["execution"],
        "summary": info["summary"],
        "archive": {
            "directory": info["directory"],
            "result": info["archive"],
            "report": info["report"],
        },
        "published": info["published"],
        "published_file": info["published_file"],
    }


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE}/{VERSION}"

    # ---- helpers ----------------------------------------------------------

    @property
    def _config(self) -> dict:
        return self.server.bridge_config  # type: ignore[attr-defined]

    def _cors_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin or "://" not in origin:
            return None
        host = origin.split("://", 1)[1].split(":", 1)[0]
        return origin if host in _LOCAL_HOSTS else None

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise run_errors.InvalidParametersError("empty request body")
        if length > MAX_BODY_BYTES:
            raise run_errors.InvalidParametersError("request body too large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ---- routing ----------------------------------------------------------

    def do_OPTIONS(self):  # noqa: N802 - stdlib naming
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - stdlib naming
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        parts = [p for p in route.split("/") if p]
        try:
            if route == "/health":
                self._send_json(200, {"status": "ok", "service": SERVICE, "version": VERSION})
            elif route == "/strategies":
                self._send_json(200, {"strategies": list_strategies(self._config["strategy_dir"])})
            elif route == "/datasets":
                self._send_json(200, {"datasets": list_datasets(self._config["data_dir"])})
            elif route == "/runs":
                self._send_json(200, {"runs": run_library.list_summaries(
                    self._config["runs"], self._config["public"])})
            elif route == "/optimizations":
                self._send_json(200, {"studies": optimization.list_studies(self._config["studies"])})
            elif len(parts) == 2 and parts[0] == "optimizations":
                self._send_json(200, {"study": optimization.load_study(
                    self._config["studies"], unquote(parts[1]))})
            elif len(parts) == 2 and parts[0] == "runs":
                self._send_json(200, {"run": run_library.run_detail(
                    self._config["runs"], unquote(parts[1]), self._config["public"])})
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "report":
                path = run_library.report_path(self._config["runs"], unquote(parts[1]))
                self._send_bytes(200, path.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_error(404, "not_found", f"unknown route: {route}")
        except Exception as exc:  # noqa: BLE001
            status, code, message = _error_for(exc)
            if status >= 500:
                traceback.print_exc()
            self._send_error(status, code, message)

    def do_POST(self):  # noqa: N802 - stdlib naming
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        parts = [p for p in route.split("/") if p]
        is_publish = len(parts) == 3 and parts[0] == "runs" and parts[2] == "publish"
        if route not in ("/runs", "/strategies/import", "/comparisons", "/optimizations") and not is_publish:
            self._send_error(404, "not_found", f"unknown route: {route}")
            return
        try:
            if route == "/strategies/import":
                payload = self._read_json_body()
                entry = import_strategy_payload(payload, self._config["strategy_dir"])
                self._send_json(200, {"strategy": entry})
                return
            if route == "/comparisons":
                payload = self._read_json_body()
                run_ids = payload.get("run_ids") if isinstance(payload, dict) else None
                data = run_comparison.compare_runs(
                    self._config["runs"], self._config["public"], run_ids)
                self._send_json(200, data)
                return
            if route == "/optimizations":
                payload = self._read_json_body()
                request = optimization.parse_optimization_payload(payload)
                study = optimization.run_study(
                    request, runs=self._config["runs"], studies=self._config["studies"],
                    public=self._config["public"])
                self._send_json(200, {"study": study})
                return
            if is_publish:
                info = run_library.publish_archived(
                    unquote(parts[1]), self._config["runs"], self._config["public"])
                self._send_json(200, {
                    "run_id": info["run_id"],
                    "published": str(info["published"]),
                    "wrote_manifest": info["wrote_manifest"],
                })
                return
            payload = self._read_json_body()
            request = run_request.parse_request_payload(payload)
            info = run_backtest.execute_run(
                request,
                out=self._config["runs"],
                report=True,
                public=self._config["public"],
            )
            self._send_json(200, _compact_run_response(info))
        except Exception as exc:  # noqa: BLE001
            status, code, message = _error_for(exc)
            if status >= 500:
                traceback.print_exc()
            self._send_error(status, code, message)

    def do_DELETE(self):  # noqa: N802 - stdlib naming
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        parts = [p for p in route.split("/") if p]
        if not (len(parts) == 2 and parts[0] in ("runs", "strategies")):
            self._send_error(404, "not_found", f"unknown route: {route}")
            return
        try:
            name = unquote(parts[1])
            if parts[0] == "runs":
                info = run_library.delete_run(
                    self._config["runs"], name, self._config["public"])
            else:
                info = strategy_library.delete_imported_strategy(
                    name, self._config["strategy_dir"])
            self._send_json(200, info)
        except Exception as exc:  # noqa: BLE001
            status, code, message = _error_for(exc)
            if status >= 500:
                traceback.print_exc()
            self._send_error(status, code, message)

    def log_message(self, fmt, *args):  # keep the bridge quiet by default
        return


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    runs: str | Path = "runs",
    public: str | Path = run_backtest.publish.DEFAULT_PUBLIC,
    strategy_dir: str | Path = DEFAULT_STRATEGY_DIR,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    studies: str | Path = optimization.DEFAULT_STUDIES,
) -> ThreadingHTTPServer:
    """Create (but do not start) a localhost bridge server.

    Use ``port=0`` to bind an ephemeral port (used by tests).
    """
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.bridge_config = {  # type: ignore[attr-defined]
        "runs": str(runs),
        "public": str(public),
        "strategy_dir": str(strategy_dir),
        "data_dir": str(data_dir),
        "studies": str(studies),
    }
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local HTTP bridge for Quant Backtest Lab.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host (localhost only by default)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runs", default="runs", help="run archive directory")
    parser.add_argument("--public", default=run_backtest.publish.DEFAULT_PUBLIC,
                        help="frontend public directory used when a run is published")
    parser.add_argument("--strategy-dir", default=DEFAULT_STRATEGY_DIR,
                        help="trusted local strategy directory listed by GET /strategies")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="approved local market-data directory listed by GET /datasets")
    parser.add_argument("--studies", default=optimization.DEFAULT_STUDIES,
                        help="optimization study archive directory")
    args = parser.parse_args(argv)

    if args.host not in _LOCAL_HOSTS:
        parser.error(f"refusing to bind non-local host {args.host!r}; use 127.0.0.1")

    server = create_server(
        args.host, args.port, runs=args.runs, public=args.public,
        strategy_dir=args.strategy_dir, data_dir=args.data_dir, studies=args.studies,
    )
    host, port = server.server_address[0], server.server_address[1]
    print(f"{SERVICE} {VERSION} listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# E2E / Browser Test Harness Safety

All browser-based test scripts in `scripts/` (`e2e_*.py`, `ui_*.py`) run through a
small shared harness defined in `scripts/e2e_run_switch.py`. Its purpose is to make
test execution **serial, bounded, and isolated from the user's personal Chrome
session**.

## Guarantees

| Concern | Behaviour |
|---|---|
| Concurrent runs | One exclusive lock per repository (`%TEMP%/qbl-e2e-<repo-hash>.lock`). A second invocation exits with code `2` and a `REFUSED` message. |
| Bridge port | The frontend is pinned to `127.0.0.1:8765`, so a dynamic bridge port is not practical. The port is validated as free before start; if it is occupied the run fails clearly instead of attaching to a foreign/newer bridge. |
| Chrome DevTools port | Selected dynamically from a free loopback port for every run. Never reused from a stale browser. |
| Browser profile | A unique temporary profile per run (`%TEMP%/qbl-e2e-<pid>-*`), passed with `--user-data-dir`. |
| Profile cleanup | The profile created by the run is deleted in `ChromeSession.terminate()` — on success and on failure. If deletion fails, the exact path is printed and the run continues. |
| Process ownership | Only processes created by this run are terminated: the `subprocess.Popen` handle of the test-owned Chrome and of the test-owned bridge. **No process is ever killed by image/name.** |
| Deadline | Every run has an overall deadline (`DEFAULT_E2E_DEADLINE_S = 900`). On expiry the run stops its owned processes, removes the owned profile, and exits with code `124`. |
| Observability | Child PIDs, selected ports, exit codes, lock state, and cleanup result are printed as `[harness] ...` lines. |

## What the harness never does

- `taskkill /F /IM chrome.exe`, `pkill`, `killall`, or any name-based kill.
- Touching `%LOCALAPPDATA%` Chrome profiles or the user's running Chrome.
- Deleting files outside the run's own temporary profile/directories.
- Running tests in parallel.

## Usage

Lightweight tests (no browser):

```bash
python -m pytest tests -q
cd Quant-Backtest-Lab && node scripts/test.mjs
```

Browser E2E (serial; requires the frontend dev server, and for scripts that own a
bridge, a free port 8765):

```bash
python scripts/e2e_chart_interaction.py --url http://127.0.0.1:5173/
python scripts/e2e_run_jobs.py
```

Run them **one at a time**. A second invocation while one is active is refused by
the lock rather than silently sharing the first run's browser or bridge.

## Port 8765 is busy

Stop the other bridge (for example the app started by `START_QUANT_LAB.bat`) before
running a bridge-owning E2E script. The harness will not attach to it, because two
bridges on one port would silently mix run data.

## Known limitations

- The lock prevents concurrent *harness-managed* runs; it cannot stop someone
  starting two bridges manually.
- `ChromeSession.terminate()` sends a normal terminate to the browser process and,
  after a grace period, force-terminates **that PID only**. If Chrome has already
  crashed, its OS-level child processes may exit on their own; they are never
  cleaned up by name.
- A hard kill of the test process (for example by the OS) cannot run cleanup; a
  stale profile directory may remain under `%TEMP%`. It can be removed manually.

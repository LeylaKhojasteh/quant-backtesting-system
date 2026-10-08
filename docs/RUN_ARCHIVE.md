# Run Archive & Study Archives

## Run archive

Every run is written to `runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `config.json` | Effective configuration: strategy selector/version, effective parameters, market (symbol, logical `dataset_id`, timeframe, range), execution settings, Account Model + instrument, `data_fingerprint`, and `data_provenance` for Data Engine runs. |
| `result.json` | The BacktestResult contract: run id, config, trades, equity, metrics, data quality, execution info, optional account report. |
| `trades.csv` | Trade table (CSV). |
| `equity.csv` | Per-trade cumulative PnL (and realized balance when an account model is present). |
| `report.html` | Standalone HTML report (no external dependencies). |
| `strategy.py` | Strategy snapshot (source) for reproducibility. |

Raw ticks are **not** copied into the archive; data provenance in `config.json`
references the logical dataset and the tick/candle cache fingerprints used.

Archives are atomic: a run directory is only presented as completed after its
artifacts are written. Publishing copies the artifact to
`Quant-Backtest-Lab/public/runs-<run_id>-result.json` and only then updates
`adapters-run-manifest.json`.

## Study archives

Lightweight manifests that reference existing runs (no duplicated results):

- `studies/study_<id>/study.json` — optimization studies.
- `walkforwards/wf_<id>/study.json` — walk-forward studies.
- `montecarlo/mc_<id>/study.json` — Monte Carlo studies.

## Legacy compatibility

Historical runs without `dataset_id`, account fields, or `data_provenance`
(e.g. `run_0001`) remain readable and are never destructively migrated.

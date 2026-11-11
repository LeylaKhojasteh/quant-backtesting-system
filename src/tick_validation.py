"""Read-only tick validation.

This module never mutates the frame, fills gaps, removes duplicates, reorders
rows, or writes files. It only reports counts/samples; fatal problems are
returned to the run boundary, which stops before the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

MAX_SAMPLES = 5

#: Version of the serialised partition-validation document. Bumped when the
#: report shape changes so stale cache files are never trusted.
PARTITION_VALIDATION_SCHEMA = 1


@dataclass
class ValidationReport:
    validation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def fatal(self) -> bool:
        return bool(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PARTITION_VALIDATION_SCHEMA,
            "validation": dict(self.validation),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "samples": {k: list(v) for k, v in self.samples.items()},
        }

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "ValidationReport":
        return cls(
            validation=dict(doc.get("validation", {})),
            warnings=list(doc.get("warnings", [])),
            errors=list(doc.get("errors", [])),
            samples={k: list(v) for k, v in doc.get("samples", {}).items()},
        )


def combine_partition_reports(reports: list[ValidationReport]) -> ValidationReport:
    """Combine per-partition reports into one run-level report.

    Counts add up; maxima take the max; boolean flags AND/OR across parts;
    gap/interval samples keep the largest MAX_SAMPLES entries; warnings and
    errors concatenate (deduped, order-preserving).
    """
    out = ValidationReport(validation={
        "ticks_finite": True,
        "timestamps_valid": True,
        "bid_le_ask": True,
        "non_finite_bid": 0,
        "non_finite_ask": 0,
        "invalid_timestamps": 0,
        "bid_gt_ask_count": 0,
        "gap_count": 0,
        "gap_max_seconds": 0.0,
        "interval_max_seconds": 0.0,
        "duplicate_timestamp_groups": 0,
        "duplicate_timestamp_rows": 0,
        "identical_row_duplicates": 0,
        "unsorted_pairs": 0,
    })
    gap_samples: list[dict[str, Any]] = []
    for rep in reports:
        v = rep.validation
        out.validation["ticks_finite"] = bool(out.validation["ticks_finite"] and v.get("ticks_finite", True))
        out.validation["timestamps_valid"] = bool(
            out.validation["timestamps_valid"] and v.get("timestamps_valid", True))
        out.validation["bid_le_ask"] = bool(out.validation["bid_le_ask"] and v.get("bid_le_ask", True))
        for key in ("non_finite_bid", "non_finite_ask", "invalid_timestamps",
                    "bid_gt_ask_count", "gap_count",
                    "duplicate_timestamp_groups", "duplicate_timestamp_rows",
                    "identical_row_duplicates", "unsorted_pairs"):
            out.validation[key] = int(out.validation[key]) + int(v.get(key, 0))
        for key in ("gap_max_seconds", "interval_max_seconds"):
            out.validation[key] = max(float(out.validation[key]), float(v.get(key, 0.0)))
        gap_samples.extend(rep.samples.get("gap", []))
        for key, rows in rep.samples.items():
            if key == "gap":
                continue
            out.samples.setdefault(key, []).extend(rows)
    gap_samples.sort(key=lambda s: float(s.get("seconds", 0.0)), reverse=True)
    if gap_samples:
        out.samples["gap"] = gap_samples[:MAX_SAMPLES]
    for rep in reports:
        for msg in rep.warnings:
            if msg not in out.warnings:
                out.warnings.append(msg)
        for msg in rep.errors:
            if msg not in out.errors:
                out.errors.append(msg)
    return out


def _index_value(index, pos: int):
    value = index[pos]
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _row(frame: pd.DataFrame, pos: int) -> dict[str, Any]:
    return {
        "row": _index_value(frame.index, pos),
        "timestamp": str(frame.iloc[pos].get("timestamp")),
        "bid": frame.iloc[pos].get("bid"),
        "ask": frame.iloc[pos].get("ask"),
    }


def _sample(frame: pd.DataFrame, positions: list[int], limit: int = MAX_SAMPLES) -> list[dict]:
    return [_row(frame, int(p)) for p in positions[:limit]]


def _sample_text(frame: pd.DataFrame, positions: list[int], limit: int = 3) -> str:
    return "; ".join(
        f"row {_index_value(frame.index, int(p))} @ {frame.iloc[int(p)].get('timestamp')} "
        f"bid={frame.iloc[int(p)].get('bid')} ask={frame.iloc[int(p)].get('ask')}"
        for p in positions[:limit]
    )


def _fmt_seconds(value: float) -> str:
    if value >= 3600:
        return f"{value / 3600:.2f}h"
    if value >= 60:
        return f"{value / 60:.2f}m"
    return f"{value:.3f}s"


def validate_ticks(frame: pd.DataFrame) -> ValidationReport:
    """Return a read-only report for a canonical tick frame."""
    report = ValidationReport(validation={
        "ticks_finite": True,
        "timestamps_valid": True,
        "bid_le_ask": True,
        "non_finite_bid": 0,
        "non_finite_ask": 0,
        "invalid_timestamps": 0,
        "bid_gt_ask_count": 0,
        "gap_count": 0,
        "gap_max_seconds": 0.0,
        "interval_max_seconds": 0.0,
        "duplicate_timestamp_groups": 0,
        "duplicate_timestamp_rows": 0,
        "identical_row_duplicates": 0,
        "unsorted_pairs": 0,
    })

    missing = [c for c in ("timestamp", "bid", "ask") if c not in frame.columns]
    if missing:
        report.errors.append(f"missing required tick column(s): {', '.join(missing)}")
        for key in ("ticks_finite", "timestamps_valid", "bid_le_ask"):
            report.validation[key] = False
        return report

    # Timestamp validity: datetime dtype and no NaT.
    ts = frame["timestamp"]
    if not pd.api.types.is_datetime64_any_dtype(ts):
        report.validation["timestamps_valid"] = False
        report.validation["invalid_timestamps"] = int(len(frame))
        bad = list(range(min(len(frame), MAX_SAMPLES)))
        report.samples["invalid_timestamp"] = _sample(frame, bad)
        report.errors.append(
            f"invalid timestamp column: expected datetime64, got {ts.dtype}; "
            f"e.g. {_sample_text(frame, bad)}"
        )
        ts_valid = None
    else:
        bad_ts = np.where(ts.isna().to_numpy())[0].tolist()
        if bad_ts:
            report.validation["timestamps_valid"] = False
            report.validation["invalid_timestamps"] = len(bad_ts)
            report.samples["invalid_timestamp"] = _sample(frame, bad_ts)
            report.errors.append(
                f"invalid/NaT timestamp: {len(bad_ts)} row(s); "
                f"e.g. {_sample_text(frame, bad_ts)}"
            )
        ts_valid = ts

    # Numeric/finite and crossed quotes (read-only conversion; source unchanged).
    bid = pd.to_numeric(frame["bid"], errors="coerce")
    ask = pd.to_numeric(frame["ask"], errors="coerce")
    bad_bid = np.where(~np.isfinite(bid.to_numpy(dtype="float64", na_value=np.nan)))[0].tolist()
    bad_ask = np.where(~np.isfinite(ask.to_numpy(dtype="float64", na_value=np.nan)))[0].tolist()
    if bad_bid or bad_ask:
        report.validation["ticks_finite"] = False
        report.validation["non_finite_bid"] = len(bad_bid)
        report.validation["non_finite_ask"] = len(bad_ask)
        positions = sorted(set(bad_bid) | set(bad_ask))
        report.samples["non_finite"] = _sample(frame, positions)
        report.errors.append(
            f"non-finite bid/ask: bid={len(bad_bid)} row(s), ask={len(bad_ask)} row(s); "
            f"e.g. {_sample_text(frame, positions)}"
        )
    finite = np.isfinite(bid.to_numpy(dtype="float64", na_value=np.nan)) & np.isfinite(
        ask.to_numpy(dtype="float64", na_value=np.nan)
    )
    crossed = np.where(finite & (ask.to_numpy(dtype="float64", na_value=np.nan) <
                                bid.to_numpy(dtype="float64", na_value=np.nan)))[0].tolist()
    if crossed:
        report.validation["bid_le_ask"] = False
        report.validation["bid_gt_ask_count"] = len(crossed)
        report.samples["bid_gt_ask"] = _sample(frame, crossed)
        report.errors.append(
            f"bid > ask: {len(crossed)} row(s); e.g. {_sample_text(frame, crossed)}"
        )

    # Non-fatal: order, intervals, duplicate timestamps, identical rows.
    if ts_valid is not None:
        prev = ts_valid.shift(1)
        unsorted_mask = ts_valid.notna() & prev.notna() & (ts_valid < prev)
        unsorted_positions = np.where(unsorted_mask.to_numpy())[0].tolist()
        report.validation["unsorted_pairs"] = len(unsorted_positions)
        if unsorted_positions:
            report.samples["unsorted"] = _sample(frame, unsorted_positions)
            report.warnings.append(
                f"unsorted timestamps: {len(unsorted_positions)} adjacent pair(s); "
                f"e.g. {_sample_text(frame, unsorted_positions)}"
            )

        valid_positions = np.where(ts_valid.notna().to_numpy())[0]
        if len(valid_positions):
            order = np.argsort(
                ts_valid.iloc[valid_positions].to_numpy(), kind="stable"
            )
            ordered_positions = valid_positions[order]
            ordered_ts = ts_valid.iloc[ordered_positions]
            deltas = ordered_ts.diff().dt.total_seconds()
            # Vectorized interval stats over one float64 array: max/top-k via
            # numpy, never a Python-level loop over every tick (a full-year run
            # holds 60M+ rows; per-row .iloc in max()/sorted() cost ~9 min).
            dvals = deltas.to_numpy(dtype="float64")
            gap_idx = np.flatnonzero(dvals > 0)
            report.validation["gap_count"] = int(len(gap_idx))
            if len(gap_idx):
                gvals = dvals[gap_idx]
                report.validation["interval_max_seconds"] = float(gvals.max())
                max_pos = int(gap_idx[int(np.argmax(gvals))])
                report.validation["gap_max_seconds"] = float(dvals[max_pos])
                # Sample the largest intervals, with the row on both sides.
                k = min(MAX_SAMPLES, len(gap_idx))
                top = gap_idx[np.argpartition(-gvals, k - 1)[:k]]
                examples = top[np.argsort(-dvals[top], kind="stable")].tolist()
                samples = []
                for p in examples:
                    samples.append({
                        "from_row": _index_value(frame.index, int(ordered_positions[p - 1])),
                        "to_row": _index_value(frame.index, int(ordered_positions[p])),
                        "from_timestamp": str(ordered_ts.iloc[p - 1]),
                        "to_timestamp": str(ordered_ts.iloc[p]),
                        "seconds": float(deltas.iloc[p]),
                    })
                report.samples["gap"] = samples
                report.warnings.append(
                    f"inter-tick intervals reported: {len(gap_idx)} positive interval(s); "
                    f"max {_fmt_seconds(float(deltas.iloc[max_pos]))} "
                    f"({samples[0]['from_timestamp']} -> {samples[0]['to_timestamp']}, "
                    f"rows {samples[0]['from_row']}-> {samples[0]['to_row']})"
                )

        duplicate_mask = ts_valid.duplicated(keep=False) & ts_valid.notna()
        duplicate_positions = np.where(duplicate_mask.to_numpy())[0].tolist()
        if duplicate_positions:
            groups: dict[str, list[int]] = {}
            for pos in duplicate_positions:
                groups.setdefault(str(ts_valid.iloc[pos]), []).append(int(pos))
            report.validation["duplicate_timestamp_groups"] = len(groups)
            report.validation["duplicate_timestamp_rows"] = len(duplicate_positions)
            sample_positions = [
                p for group in list(groups.values())[:MAX_SAMPLES] for p in group
            ]
            report.samples["duplicate_timestamp"] = _sample(frame, sample_positions)
            report.warnings.append(
                f"duplicate timestamps kept as-is: {len(groups)} group(s), "
                f"{len(duplicate_positions)} row(s); "
                f"e.g. {_sample_text(frame, duplicate_positions)}"
            )

    identical_mask = frame.duplicated(subset=["timestamp", "bid", "ask"], keep="first")
    identical_positions = np.where(identical_mask.to_numpy())[0].tolist()
    if identical_positions:
        report.validation["identical_row_duplicates"] = len(identical_positions)
        report.samples["identical_rows"] = _sample(frame, identical_positions)
        report.warnings.append(
            f"identical duplicate rows kept as-is: {len(identical_positions)} row(s); "
            f"e.g. {_sample_text(frame, identical_positions)}"
        )

    return report

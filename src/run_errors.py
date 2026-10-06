"""Structured errors for the research workflow and the local bridge.

Reference: ``ARCHITECTURE.md`` (engine stays independent; contracts, not
tracebacks, cross the boundary).

Every error carries a stable machine ``code`` and an ``http_status`` so the
bridge can return a clear JSON error without leaking Python tracebacks. All
classes derive from ``ValueError`` so existing CLI code and tests that expect
``ValueError`` keep working unchanged.
"""

from __future__ import annotations


class RunError(ValueError):
    """Base class for expected, user-facing research workflow errors."""

    code = "run_error"
    http_status = 500

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = str(message)


class UnknownStrategyError(RunError):
    code = "unknown_strategy"
    http_status = 404


class InvalidStrategyError(RunError):
    code = "invalid_strategy"
    http_status = 400


class InvalidParametersError(RunError):
    code = "invalid_parameters"
    http_status = 400


class InvalidDatasetError(RunError):
    code = "invalid_dataset"
    http_status = 400


class InvalidExecutionError(RunError):
    code = "invalid_execution"
    http_status = 400


class ResultValidationError(RunError):
    code = "result_validation_failed"
    http_status = 500


class BacktestFailedError(RunError):
    code = "backtest_failed"
    http_status = 500


class ArchiveFailedError(RunError):
    code = "archive_failed"
    http_status = 500


class ReportFailedError(RunError):
    code = "report_failed"
    http_status = 500


class PublishFailedError(RunError):
    code = "publish_failed"
    http_status = 500


class RunNotFoundError(RunError):
    code = "run_not_found"
    http_status = 404


class InvalidRunIdError(RunError):
    code = "invalid_run_id"
    http_status = 400


class ReportUnavailableError(RunError):
    code = "report_unavailable"
    http_status = 404


class RunDeleteError(RunError):
    code = "run_delete_failed"
    http_status = 400


class StrategyNotFoundError(RunError):
    code = "strategy_not_found"
    http_status = 404


class BuiltinStrategyError(RunError):
    code = "builtin_strategy_protected"
    http_status = 400


class StrategyDeleteError(RunError):
    code = "strategy_delete_failed"
    http_status = 400


class InvalidOptimizationError(RunError):
    code = "invalid_optimization"
    http_status = 400


class CandidateLimitError(RunError):
    code = "candidate_limit_exceeded"
    http_status = 400


class StudyNotFoundError(RunError):
    code = "study_not_found"
    http_status = 404


class InvalidStudyIdError(RunError):
    code = "invalid_study_id"
    http_status = 400


class InvalidWalkForwardError(RunError):
    code = "invalid_walk_forward"
    http_status = 400


class WalkForwardLimitError(RunError):
    code = "walk_forward_limit_exceeded"
    http_status = 400


class WalkForwardNotFoundError(RunError):
    code = "walk_forward_not_found"
    http_status = 404


class InvalidWalkForwardIdError(RunError):
    code = "invalid_walk_forward_id"
    http_status = 400

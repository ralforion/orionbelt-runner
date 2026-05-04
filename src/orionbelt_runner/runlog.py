"""Run log: a per-run YAML sidecar capturing executed SQL + OBSL explanations.

Coexists with the markdown report. Always emitted (success or failure) — the
runlog is most valuable when the report didn't make it. Pure function:
``render_runlog(log)`` takes a fully-populated ``RunLog`` and returns the
YAML text; the ``Runner`` is the only place that writes it to disk.

Format choice: YAML (not markdown-with-fenced-blocks) so it's both human-
skimmable and trivially machine-parseable in one step. SQL strings are
emitted as block scalars (``|``) so they keep their newlines.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

from orionbelt_runner import __version__
from orionbelt_runner.client import ExecuteResult


@dataclass
class QueryLogEntry:
    """One query's slice of the run log.

    ``error`` is set instead of the result fields when the query failed.
    Wall-clock ``duration_ms`` is the runner's measurement (network + server);
    ``server_time_ms`` mirrors OBSL's ``execution_time_ms`` (server only).
    """

    name: str
    dialect: str
    started_at: datetime
    duration_ms: float
    error: str | None = None
    result: ExecuteResult | None = None


@dataclass
class ObslMeta:
    """OBSL connection + version info captured at run time."""

    base_url: str
    version: str | None = None
    api_version: str | None = None
    session_id: str | None = None
    model_id: str | None = None
    locale: str | None = None
    timezone: str | None = None


@dataclass
class RunLog:
    """Top-level structure of the run log file."""

    spec: str
    description: str | None
    started_at: datetime
    finished_at: datetime
    obsl: ObslMeta
    queries: list[QueryLogEntry] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    report_path: str | None = None
    # Defaults to the running orionbelt_runner version. Override (e.g. in
    # tests) by passing an explicit value at construction time.
    runner_version: str = field(default_factory=lambda: __version__)

    @property
    def duration_ms(self) -> float:
        delta = self.finished_at - self.started_at
        return delta.total_seconds() * 1000.0


def render_runlog(log: RunLog) -> str:
    """Render a RunLog to YAML text."""
    payload = _build_payload(log)
    # Round-trip dumper so LiteralScalarString renders as a `|` block scalar
    # (the safe dumper rejects it). We're only writing, never reading, so the
    # round-trip overhead is irrelevant here.
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 4096  # don't fold long single-line strings (warnings, reasons)
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = io.StringIO()
    yaml.dump(payload, buf)
    return buf.getvalue()


def _build_payload(log: RunLog) -> dict[str, Any]:
    return {
        "spec": log.spec,
        "description": log.description,
        "runner_version": log.runner_version,
        "started_at": log.started_at.isoformat(),
        "finished_at": log.finished_at.isoformat(),
        "duration_ms": round(log.duration_ms, 3),
        "report_path": log.report_path,
        "obsl": _obsl_payload(log.obsl),
        "errors": dict(log.errors),
        "queries": [_query_payload(q) for q in log.queries],
    }


def _obsl_payload(obsl: ObslMeta) -> dict[str, Any]:
    return {
        "base_url": obsl.base_url,
        "version": obsl.version,
        "api_version": obsl.api_version,
        "session_id": obsl.session_id,
        "model_id": obsl.model_id,
        "locale": obsl.locale,
        "timezone": obsl.timezone,
    }


def _query_payload(entry: QueryLogEntry) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": entry.name,
        "dialect": entry.dialect,
        "started_at": entry.started_at.isoformat(),
        "duration_ms": round(entry.duration_ms, 3),
    }
    if entry.error is not None:
        base["error"] = entry.error
        return base
    result = entry.result
    if result is None:
        # Defensive: a non-error entry without a result shouldn't happen,
        # but emit a clear placeholder rather than crashing the dump.
        base["error"] = "no result captured"
        return base
    base.update(
        {
            "row_count": result.row_count,
            "server_time_ms": round(result.execution_time_ms, 3),
            "timezone": result.timezone,
            "sql_valid": result.sql_valid,
            "warnings": list(result.warnings),
            "sql": LiteralScalarString(result.sql) if result.sql else "",
            "resolved": {
                "fact_tables": list(result.resolved.fact_tables),
                "dimensions": list(result.resolved.dimensions),
                "measures": list(result.resolved.measures),
            },
            "explain": _explain_payload(result),
        }
    )
    return base


def _explain_payload(result: ExecuteResult) -> dict[str, Any] | None:
    plan = result.explain
    if plan is None:
        return None
    return {
        "planner": plan.planner,
        "planner_reason": plan.planner_reason,
        "base_object": plan.base_object,
        "base_object_reason": plan.base_object_reason,
        "where_filter_count": plan.where_filter_count,
        "having_filter_count": plan.having_filter_count,
        "has_totals": plan.has_totals,
        "joins": [
            {
                "from": j.from_object,
                "to": j.to_object,
                "columns": list(j.join_columns),
                "reason": j.reason,
            }
            for j in plan.joins
        ],
        "cfl_legs": [
            {
                "measure_source": leg.measure_source,
                "common_root": leg.common_root,
                "reason": leg.reason,
                "measures": list(leg.measures),
                "joins": list(leg.joins),
            }
            for leg in plan.cfl_legs
        ],
    }

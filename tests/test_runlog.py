"""Tests for the run-log YAML renderer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ruamel.yaml import YAML

from orionbelt_runner.client import (
    ExecuteResult,
    ExplainCflLeg,
    ExplainJoin,
    ExplainPlan,
    ResolvedInfo,
)
from orionbelt_runner.runlog import (
    ObslMeta,
    QueryLogEntry,
    RunLog,
    render_runlog,
)


def _make_log(*, queries: list[QueryLogEntry], errors: dict[str, str] | None = None) -> RunLog:
    started = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
    return RunLog(
        spec="monthly-revenue",
        description="Demo run",
        started_at=started,
        finished_at=started + timedelta(seconds=3),
        obsl=ObslMeta(
            base_url="http://localhost:8080",
            version="2.1.0",
            api_version="v1",
            session_id="sess-1",
            model_id="model-loaded",
            locale="en-US",
            timezone="Europe/Berlin",
        ),
        queries=queries,
        errors=errors or {},
        report_path="out/report.md",
    )


def _ok_result() -> ExecuteResult:
    return ExecuteResult(
        sql="SELECT country, SUM(revenue) AS total\nFROM orders\nGROUP BY country",
        dialect="postgres",
        columns=[{"name": "country", "type": "string"}, {"name": "total", "type": "number"}],
        rows=[["DE", 5000], ["US", 7345]],
        row_count=2,
        execution_time_ms=12.4,
        timezone="Europe/Berlin",
        warnings=["totals truncated"],
        sql_valid=True,
        resolved=ResolvedInfo(
            fact_tables=["orders"],
            dimensions=["country"],
            measures=["total"],
        ),
        explain=ExplainPlan(
            planner="DefaultPlanner",
            planner_reason="single fact table",
            base_object="orders",
            base_object_reason="only fact in select",
            joins=[
                ExplainJoin(
                    from_object="orders",
                    to_object="customers",
                    join_columns=["customer_id"],
                    reason="needed for country",
                ),
            ],
            where_filter_count=1,
            having_filter_count=0,
            has_totals=False,
            cfl_legs=[
                ExplainCflLeg(
                    measure_source="orders",
                    common_root="customers",
                    reason="single-leg",
                    measures=["total"],
                ),
            ],
        ),
    )


def test_render_runlog_round_trips_to_valid_yaml() -> None:
    """The output must parse cleanly and surface the spec name + obsl version."""
    log = _make_log(
        queries=[
            QueryLogEntry(
                name="by_country",
                dialect="postgres",
                started_at=datetime(2026, 5, 4, 10, 0, 1, tzinfo=UTC),
                duration_ms=42.5,
                result=_ok_result(),
            ),
        ],
    )
    text = render_runlog(log)
    parsed = YAML(typ="safe").load(text)

    assert parsed["spec"] == "monthly-revenue"
    # Runner version defaults to the live package __version__ — assert
    # presence rather than pinning so future bumps don't break the test.
    assert parsed["runner_version"] and isinstance(parsed["runner_version"], str)
    assert parsed["obsl"]["version"] == "2.1.0"
    assert parsed["obsl"]["api_version"] == "v1"
    assert parsed["obsl"]["session_id"] == "sess-1"
    assert parsed["report_path"] == "out/report.md"
    assert parsed["duration_ms"] == 3000.0
    assert len(parsed["queries"]) == 1
    q = parsed["queries"][0]
    assert q["name"] == "by_country"
    assert q["row_count"] == 2
    assert q["server_time_ms"] == 12.4
    assert q["sql_valid"] is True
    assert q["warnings"] == ["totals truncated"]
    assert q["resolved"]["fact_tables"] == ["orders"]
    assert q["explain"]["planner"] == "DefaultPlanner"
    assert q["explain"]["joins"][0]["from"] == "orders"
    assert q["explain"]["joins"][0]["to"] == "customers"
    assert q["explain"]["cfl_legs"][0]["measure_source"] == "orders"


def test_render_runlog_emits_sql_as_block_scalar() -> None:
    """Multi-line SQL must render as a ``|`` block scalar so it stays readable."""
    log = _make_log(
        queries=[
            QueryLogEntry(
                name="by_country",
                dialect="postgres",
                started_at=datetime(2026, 5, 4, 10, 0, 1, tzinfo=UTC),
                duration_ms=10.0,
                result=_ok_result(),
            ),
        ],
    )
    text = render_runlog(log)
    # Expect the literal block scalar marker on the sql field.
    assert "sql: |" in text
    # And the SQL body shouldn't be wrapped in quotes.
    assert "'SELECT country" not in text
    assert '"SELECT country' not in text


def test_render_runlog_records_errors() -> None:
    """Failed queries must appear with an ``error`` field and no result fields."""
    log = _make_log(
        queries=[
            QueryLogEntry(
                name="broken",
                dialect="postgres",
                started_at=datetime(2026, 5, 4, 10, 0, 1, tzinfo=UTC),
                duration_ms=2.0,
                error="RuntimeError: boom",
            ),
        ],
        errors={"broken": "RuntimeError: boom"},
    )
    text = render_runlog(log)
    parsed = YAML(typ="safe").load(text)
    assert parsed["errors"] == {"broken": "RuntimeError: boom"}
    q = parsed["queries"][0]
    assert q["error"] == "RuntimeError: boom"
    assert "sql" not in q  # no result-shaped fields when the query failed
    assert "explain" not in q


def test_render_runlog_explain_optional() -> None:
    """``explain`` may be ``None`` (older OBSL or queries that don't compile a plan)."""
    result = _ok_result()
    result.explain = None
    log = _make_log(
        queries=[
            QueryLogEntry(
                name="by_country",
                dialect="postgres",
                started_at=datetime(2026, 5, 4, 10, 0, 1, tzinfo=UTC),
                duration_ms=1.0,
                result=result,
            ),
        ],
    )
    parsed = YAML(typ="safe").load(render_runlog(log))
    assert parsed["queries"][0]["explain"] is None

"""CLI for the lab."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from time import perf_counter
from typing import Annotated, Protocol, cast

import typer
import yaml  # type: ignore[import-untyped]

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .report import write_report
from .scenarios import load_scenarios
from .state import Route, Scenario, initial_state

app = typer.Typer(no_args_is_help=True)


class RunnableGraph(Protocol):
    def invoke(
        self,
        state: object,
        config: Mapping[str, object],
    ) -> dict[str, object]:
        ...

    def get_state_history(self, config: Mapping[str, object]) -> Iterable[object]:
        ...

    def get_state(self, config: Mapping[str, object]) -> object:
        ...


def _load_yaml_config(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise typer.BadParameter("Config file must be a YAML object")
    return cast(dict[str, object], raw)


def _build_runnable_graph(cfg: Mapping[str, object]) -> RunnableGraph:
    checkpointer = build_checkpointer(
        str(cfg.get("checkpointer", "memory")),
        cast(str | None, cfg.get("database_url")),
    )
    return cast(RunnableGraph, build_graph(checkpointer=checkpointer))


def _safe_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _interrupts_payload(result: Mapping[str, object]) -> list[dict[str, object]]:
    raw = result.get("__interrupt__", [])
    if not isinstance(raw, list):
        return []
    payload: list[dict[str, object]] = []
    for item in raw:
        payload.append(
            {
                "id": getattr(item, "id", None),
                "value": getattr(item, "value", None),
            }
        )
    return payload


def _start_hitl_run(graph: RunnableGraph, thread: str, query: str) -> dict[str, object]:
    os.environ["LANGGRAPH_INTERRUPT"] = "true"
    scenario = Scenario(
        id=thread,
        query=query,
        expected_route=Route.RISKY,
        requires_approval=True,
    )
    state = initial_state(scenario)
    run_config = {"configurable": {"thread_id": state["thread_id"]}}
    result = graph.invoke(state, config=run_config)
    snapshot = graph.get_state(config=run_config)
    next_nodes = list(getattr(snapshot, "next", ()))
    events = result.get("events", [])
    events_count = len(events) if isinstance(events, list) else 0
    return {
        "thread_id": state["thread_id"],
        "interrupted": "__interrupt__" in result,
        "next_nodes": next_nodes,
        "interrupts": _interrupts_payload(result),
        "state_preview": {
            "route": result.get("route"),
            "proposed_action": result.get("proposed_action"),
            "events_count": events_count,
        },
    }


def _resume_hitl_run(
    graph: RunnableGraph,
    thread_id: str,
    action: str,
    reviewer: str,
    comment: str,
    edited_action: str,
) -> dict[str, object]:
    from langgraph.types import Command

    os.environ["LANGGRAPH_INTERRUPT"] = "true"
    payload = {
        "action": action,
        "reviewer": reviewer,
        "comment": comment,
        "edited_action": edited_action,
    }
    run_config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(Command(resume=payload), config=run_config)
    return {
        "thread_id": thread_id,
        "route": result.get("route"),
        "approval": result.get("approval"),
        "final_answer": result.get("final_answer"),
        "pending_question": result.get("pending_question"),
        "interrupts": _interrupts_payload(result),
        "errors": result.get("errors", []),
    }


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    cfg = _load_yaml_config(config)
    checkpointer_kind = str(cfg.get("checkpointer", "memory"))
    scenarios = load_scenarios(str(cfg["scenarios_path"]))
    graph = _build_runnable_graph(cfg)
    metrics = []
    resume_success = False

    for scenario in scenarios:
        state = initial_state(scenario)
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        start_time = perf_counter()
        final_state = graph.invoke(state, config=run_config)
        latency_ms = int((perf_counter() - start_time) * 1000)
        metrics.append(
            metric_from_state(
                final_state,
                scenario.expected_route.value,
                scenario.requires_approval,
                latency_ms=latency_ms,
            )
        )
        if checkpointer_kind != "none" and hasattr(graph, "get_state_history"):
            history = list(graph.get_state_history(config=run_config))
            if history:
                resume_success = True

    report = summarize_metrics(metrics)
    report.resume_success = resume_success
    write_metrics(report, output)
    if cfg.get("report_path"):
        write_report(report, str(cfg["report_path"]))
    typer.echo(f"Wrote metrics to {output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


@app.command("demo-hitl-start")
def demo_hitl_start(
    config: Annotated[Path, typer.Option("--config")] = Path("configs/lab.yaml"),
    thread: Annotated[str, typer.Option("--thread")] = "demo-hitl",
    query: Annotated[str, typer.Option("--query")] = (
        "Refund this customer and send confirmation email"
    ),
) -> None:
    """Start a risky flow and stop at HITL interrupt (for demo/UI)."""
    cfg = _load_yaml_config(config)
    graph = _build_runnable_graph(cfg)
    payload = _start_hitl_run(graph, thread, query)
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("demo-hitl-resume")
def demo_hitl_resume(
    thread_id: Annotated[str, typer.Option("--thread-id")],
    config: Annotated[Path, typer.Option("--config")] = Path("configs/lab.yaml"),
    action: Annotated[str, typer.Option("--action")] = "approve",
    reviewer: Annotated[str, typer.Option("--reviewer")] = "demo-reviewer",
    comment: Annotated[str, typer.Option("--comment")] = "approved in demo",
    edited_action: Annotated[str, typer.Option("--edited-action")] = "",
) -> None:
    """Resume a paused HITL flow with approval/reject/edit decision."""
    cfg = _load_yaml_config(config)
    graph = _build_runnable_graph(cfg)
    payload = _resume_hitl_run(
        graph=graph,
        thread_id=thread_id,
        action=action,
        reviewer=reviewer,
        comment=comment,
        edited_action=edited_action,
    )
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("demo-time-travel")
def demo_time_travel(
    thread_id: Annotated[str, typer.Option("--thread-id")],
    config: Annotated[Path, typer.Option("--config")] = Path("configs/lab.yaml"),
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Show checkpoint history for one thread_id (time-travel evidence)."""
    cfg = _load_yaml_config(config)
    graph = _build_runnable_graph(cfg)
    run_config = {"configurable": {"thread_id": thread_id}}
    history = list(graph.get_state_history(config=run_config))
    snapshots = history[: max(0, limit)]

    items: list[dict[str, object]] = []
    for snapshot in snapshots:
        values = _safe_mapping(getattr(snapshot, "values", {}))
        events = values.get("events", [])
        last_node = None
        if isinstance(events, list) and events:
            maybe_last = events[-1]
            if isinstance(maybe_last, Mapping):
                last_node = maybe_last.get("node")

        snapshot_config = _safe_mapping(getattr(snapshot, "config", {}))
        configurable = _safe_mapping(snapshot_config.get("configurable", {}))

        items.append(
            {
                "checkpoint_id": configurable.get("checkpoint_id"),
                "created_at": getattr(snapshot, "created_at", None),
                "next_nodes": list(getattr(snapshot, "next", ())),
                "route": values.get("route"),
                "attempt": values.get("attempt"),
                "last_node": last_node,
            }
        )

    payload = {
        "thread_id": thread_id,
        "total_checkpoints": len(history),
        "snapshots": items,
    }
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("demo-crash-recover")
def demo_crash_recover(
    phase: Annotated[str, typer.Option("--phase")] = "start",
    config: Annotated[Path, typer.Option("--config")] = Path("configs/lab.yaml"),
    thread: Annotated[str, typer.Option("--thread")] = "demo-crash",
    query: Annotated[str, typer.Option("--query")] = (
        "Refund this customer and send confirmation email"
    ),
    action: Annotated[str, typer.Option("--action")] = "approve",
    reviewer: Annotated[str, typer.Option("--reviewer")] = "recover-reviewer",
    comment: Annotated[str, typer.Option("--comment")] = "recovered after restart",
    edited_action: Annotated[str, typer.Option("--edited-action")] = "",
) -> None:
    """Two-phase crash-recover demo using same thread_id and SQLite checkpoint."""
    cfg = _load_yaml_config(config)
    graph = _build_runnable_graph(cfg)

    if phase == "start":
        payload = _start_hitl_run(graph, thread, query)
        payload["note"] = (
            "Now stop process/app, then run --phase resume with --thread-id from payload."
        )
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    if phase == "resume":
        thread_id = f"thread-{thread}"
        payload = _resume_hitl_run(
            graph=graph,
            thread_id=thread_id,
            action=action,
            reviewer=reviewer,
            comment=comment,
            edited_action=edited_action,
        )
        payload["note"] = "Recovered from checkpoint and completed flow."
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    raise typer.BadParameter("phase must be either 'start' or 'resume'")


if __name__ == "__main__":
    app()

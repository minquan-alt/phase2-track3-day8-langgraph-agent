"""Streamlit UI for HITL demo, crash-recover, and time-travel evidence."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import streamlit as st  # type: ignore[import-not-found,import-untyped]
import yaml  # type: ignore[import-untyped]
from langgraph.types import Command

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state


@st.cache_resource
def build_demo_graph(config_path: str) -> object:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Config file must be a YAML object")
    cfg = cast(dict[str, object], raw)
    checkpointer = build_checkpointer(
        str(cfg.get("checkpointer", "sqlite")),
        cast(str | None, cfg.get("database_url")),
    )
    return build_graph(checkpointer=checkpointer)


def _safe_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _interrupt_payload(result: Mapping[str, object]) -> list[dict[str, object]]:
    raw = result.get("__interrupt__", [])
    if not isinstance(raw, list):
        return []
    return [
        {"id": getattr(item, "id", None), "value": getattr(item, "value", None)}
        for item in raw
    ]


st.set_page_config(page_title="LangGraph HITL Demo", layout="wide")
st.title("LangGraph HITL Demo UI")
st.caption(
    "Demo approval interrupt/resume, crash recovery, and time travel with SQLite checkpoints."
)

config_path = st.text_input("Config path", value="configs/lab.yaml")
graph = build_demo_graph(config_path)
os.environ["LANGGRAPH_INTERRUPT"] = "true"

left, right = st.columns(2)

with left:
    st.subheader("1) Start risky flow (interrupt)")
    run_id = st.text_input("Run ID", value="demo-ui")
    query = st.text_area(
        "Risky query",
        value="Refund this customer and send confirmation email",
        height=100,
    )
    if st.button("Start flow"):
        scenario = Scenario(
            id=run_id,
            query=query,
            expected_route=Route.RISKY,
            requires_approval=True,
        )
        state = initial_state(scenario)
        thread_id = str(state["thread_id"])
        run_config = {"configurable": {"thread_id": thread_id}}
        result = cast(dict[str, object], graph.invoke(state, config=run_config))
        st.session_state["thread_id"] = thread_id
        st.session_state["start_result"] = result
        st.success(f"Flow paused at approval. thread_id={thread_id}")
        st.json(
            {
                "interrupts": _interrupt_payload(result),
                "route": result.get("route"),
                "proposed_action": result.get("proposed_action"),
            }
        )

with right:
    st.subheader("2) Resume with human decision")
    default_thread = cast(str, st.session_state.get("thread_id", "thread-demo-ui"))
    thread_id = st.text_input("Thread ID", value=default_thread)
    action = st.selectbox("Action", options=["approve", "reject", "edit", "timeout"], index=0)
    reviewer = st.text_input("Reviewer", value="demo-reviewer")
    comment = st.text_input("Comment", value="approved via Streamlit")
    edited_action = st.text_input("Edited action (for edit)", value="")
    if st.button("Resume flow"):
        run_config = {"configurable": {"thread_id": thread_id}}
        payload = {
            "action": action,
            "reviewer": reviewer,
            "comment": comment,
            "edited_action": edited_action,
        }
        result = cast(dict[str, object], graph.invoke(Command(resume=payload), config=run_config))
        st.session_state["resume_result"] = result
        st.success("Flow resumed and completed.")
        st.json(
            {
                "approval": result.get("approval"),
                "final_answer": result.get("final_answer"),
                "pending_question": result.get("pending_question"),
                "errors": result.get("errors", []),
            }
        )

st.subheader("3) Time travel (checkpoint history)")
thread_for_history = st.text_input(
    "Thread ID for history",
    value=cast(str, st.session_state.get("thread_id", "thread-demo-ui")),
)
if st.button("Load history"):
    run_config = {"configurable": {"thread_id": thread_for_history}}
    history = list(graph.get_state_history(config=run_config))
    rows: list[dict[str, object]] = []
    for snapshot in history:
        values = _safe_mapping(getattr(snapshot, "values", {}))
        events = values.get("events", [])
        last_node = None
        if isinstance(events, list) and events:
            maybe_last = events[-1]
            if isinstance(maybe_last, Mapping):
                last_node = maybe_last.get("node")

        snapshot_config = _safe_mapping(getattr(snapshot, "config", {}))
        configurable = _safe_mapping(snapshot_config.get("configurable", {}))
        rows.append(
            {
                "checkpoint_id": configurable.get("checkpoint_id"),
                "created_at": getattr(snapshot, "created_at", None),
                "route": values.get("route"),
                "attempt": values.get("attempt"),
                "next_nodes": list(getattr(snapshot, "next", ())),
                "last_node": last_node,
            }
        )
    st.write(f"Found {len(rows)} checkpoints.")
    st.json(rows)

st.subheader("Raw state snapshots")
if "start_result" in st.session_state:
    st.markdown("**After start (interrupted):**")
    start_payload = json.dumps(
        cast(dict[str, object], st.session_state["start_result"]),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    st.code(start_payload, language="json")
if "resume_result" in st.session_state:
    st.markdown("**After resume (completed):**")
    resume_payload = json.dumps(
        cast(dict[str, object], st.session_state["resume_result"]),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    st.code(resume_payload, language="json")

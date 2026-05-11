"""Routing functions for conditional edges."""

from __future__ import annotations

from .state import AgentState, Route


def route_after_classify(state: AgentState) -> str:
    """Map classified route to the next graph node.

    TODO(student): handle unknown routes safely and update tests for edge cases.
    """
    route = state.get("route", Route.SIMPLE.value)
    mapping = {
        Route.SIMPLE.value: "answer",
        Route.TOOL.value: "tool",
        Route.MISSING_INFO.value: "clarify",
        Route.RISKY.value: "risky_action",
        Route.ERROR.value: "retry",
    }
    return mapping.get(route, "clarify")


def route_after_retry(state: AgentState) -> str:
    """Decide whether to retry, fallback, or dead-letter.

    TODO(student): implement bounded retry and dead-letter routing.
    """
    attempt = int(state.get("attempt", 0))
    max_attempts = int(state.get("max_attempts", 3))
    return "tool" if attempt < max_attempts else "dead_letter"


def route_after_evaluate(state: AgentState) -> str:
    """Decide whether tool result is satisfactory or needs retry.

    This is the 'done?' check that enables retry loops — a key LangGraph advantage over LCEL.
    TODO(student): replace heuristic with LLM-as-judge or structured validation.
    """
    return "retry" if state.get("evaluation_result") == "needs_retry" else "answer"


def route_after_approval(state: AgentState) -> str:
    """Continue only if approved.

    TODO(student): support reject/edit outcomes.
    """
    approval = state.get("approval") or {}
    return "tool" if approval.get("approved") else "clarify"

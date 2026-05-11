"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

import re

from .state import AgentState, ApprovalDecision, Route, make_event

RISKY_KEYWORDS = {"refund", "delete", "send", "cancel", "remove", "revoke"}
TOOL_KEYWORDS = {"status", "order", "lookup", "check", "track", "find", "search"}
ERROR_KEYWORDS = {"timeout", "fail", "failure", "error", "crash", "unavailable"}
VAGUE_PRONOUNS = {"it", "this", "that", "thing", "stuff"}

def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields.

    TODO(student): add normalization, PII checks, and metadata extraction.
    """
    raw_query = state.get("query", "")
    query = " ".join(raw_query.strip().split())

    return {
        "query": query,
        "messages": [f"intake:{query[:80]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    TODO(student): replace keyword heuristics with a clear routing policy.
    Required routes: simple, tool, missing_info, risky, error.
    """
    query = state.get("query", "")
    normalized = query.lower().strip()
    tokens = re.findall(r"\b\w+\b", normalized)

    route = Route.SIMPLE
    risk_level = "low"
    if any(k in tokens for k in RISKY_KEYWORDS):
        route = Route.RISKY
        risk_level = "high"
    elif any(k in tokens for k in TOOL_KEYWORDS):
        route = Route.TOOL
    elif len(tokens) < 5 and any(p in tokens for p in VAGUE_PRONOUNS):
        route = Route.MISSING_INFO
    elif any(k in tokens for k in ERROR_KEYWORDS):
        route = Route.ERROR

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"route={route.value}")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    TODO(student): generate a specific clarification question from state.
    """
    question = "Can you provide the order id or the missing context?"
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Simulates transient failures for error-route scenarios to demonstrate retry loops.
    TODO(student): implement idempotent tool execution and structured tool results.
    """
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    if state.get("route") == Route.ERROR.value and attempt < 2:
        result = f"status=error transient failure attempt={attempt} scenario={scenario_id}"
    else:
        result = f"status=ok tool result attempt={attempt} scenario={scenario_id}"
    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", f"tool executed attempt={attempt}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval.

    TODO(student): create a proposed action with evidence and risk justification.
    """
    query = str(state.get("query", "")).strip()
    scenario_id = state.get("scenario_id", "unknown")
    risk_level = state.get("risk_level", "unknown")

    proposed_action = f"Execute risky support action for scenario={scenario_id}: {query}"
    return {
        "proposed_action": proposed_action,
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required",
                scenario_id=scenario_id,
                risk_level=risk_level,
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock decision so tests and CI run offline.

    TODO(student): implement reject/edit decisions and timeout escalation.
    """
    import os

    decision_action = "mock_approve"
    edited_action = ""

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt(
            {
                "proposed_action": state.get("proposed_action"),
                "risk_level": state.get("risk_level"),
                "scenario_id": state.get("scenario_id"),
                "instructions": (
                    "Return {action: approve|reject|edit|timeout, "
                    "comment, reviewer, edited_action}"
                ),
            }
        )
        if isinstance(value, dict):
            action = str(value.get("action", "approve")).lower()
            reviewer = str(value.get("reviewer", "human-reviewer"))
            comment = str(value.get("comment", "")).strip()
            edited_action = str(value.get("edited_action", "")).strip()

            if action == "timeout":
                decision = ApprovalDecision(
                    approved=False,
                    reviewer=reviewer,
                    comment=comment or "approval timeout; escalated",
                )
                decision_action = "timeout"
            elif action == "reject":
                decision = ApprovalDecision(
                    approved=False,
                    reviewer=reviewer,
                    comment=comment or "rejected by reviewer",
                )
                decision_action = "reject"
            elif action == "edit":
                decision = ApprovalDecision(
                    approved=False,
                    reviewer=reviewer,
                    comment=comment or "please edit and resubmit",
                )
                decision_action = "edit"
            else:
                decision = ApprovalDecision(
                    approved=True,
                    reviewer=reviewer,
                    comment=comment or "approved",
                )
                decision_action = "approve"
        else:
            decision = ApprovalDecision(
                approved=bool(value),
                reviewer="human-reviewer",
                comment="binary decision",
            )
            decision_action = "approve" if decision.approved else "reject"
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")

    update: dict[str, object] = {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"{decision_action} decision recorded by {decision.reviewer}",
            )
        ],
    }
    if edited_action:
        update["proposed_action"] = edited_action

    if not decision.approved:
        update["errors"] = [f"approval_not_granted action={decision_action}"]

    return update


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision.

    TODO(student): implement bounded retry, exponential backoff metadata, and fallback route.
    """
    attempt = int(state.get("attempt", 0)) + 1
    backoff_seconds = 2 ** max(0, attempt - 1)

    return {
        "attempt": attempt,
        "errors": [f"retry attempt={attempt} backoff={backoff_seconds}s"],
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                backoff_seconds=backoff_seconds,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response.

    TODO(student): ground the answer in tool_results and approval where relevant.
    """
    tool_results = state.get("tool_results", [])
    approval = state.get("approval", {})

    if tool_results:
        suffix = " (approved)" if approval and approval.get("approved") else ""
        answer = f"I found: {tool_results[-1]}{suffix}"
    else:
        answer = "Please provide more details so I can proceed safely."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the 'done?' check that enables retry loops.

    TODO(student): replace heuristic with LLM-as-judge or structured validation.
    """
    latest = (state.get("tool_results", []) or [""])[-1]
    low = latest.lower()
    needs_retry = (
        latest.startswith("ERROR:")
        or "status=error" in low
        or "transient failure" in low
    )

    return {
        "evaluation_result": "needs_retry" if needs_retry else "success",
        "events": [
            make_event(
                "evaluate",
                "completed",
                (
                    "tool result indicates failure, retry needed"
                    if needs_retry
                    else "tool result satisfactory"
                ),
            ),
        ],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    Third layer of error strategy: retry -> fallback -> dead letter.
    TODO(student): persist to dead-letter queue, alert on-call, or create support ticket.
    """
    attempt = int(state.get("attempt", 0))
    return {
        "final_answer": (
            "Request could not be completed after maximum retry attempts. "
            "Logged for manual review."
        ),
        "errors": [f"dead-letter exhausted retries at attempt={attempt}"],
        "events": [
            make_event(
                "dead_letter",
                "completed",
                f"max retries exceeded, attempt={attempt}",
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}

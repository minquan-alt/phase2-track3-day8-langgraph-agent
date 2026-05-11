from collections import Counter
from pathlib import Path

from langgraph_agent_lab.scenarios import load_scenarios


def test_hard_scenarios_have_good_coverage() -> None:
    scenarios = load_scenarios(Path("data/sample/scenarios_hard.jsonl"))
    assert len(scenarios) >= 20

    route_counts = Counter(item.expected_route.value for item in scenarios)
    assert {"simple", "tool", "missing_info", "risky", "error"}.issubset(route_counts.keys())
    assert route_counts["risky"] >= 5
    assert route_counts["error"] >= 5

    approval_count = sum(1 for item in scenarios if item.requires_approval)
    retry_count = sum(1 for item in scenarios if item.should_retry)
    dead_letter_count = sum(1 for item in scenarios if item.max_attempts <= 2)

    assert approval_count >= 5
    assert retry_count >= 5
    assert dead_letter_count >= 2

    tags = {tag for item in scenarios for tag in item.tags}
    assert "priority" in tags
    assert "word_boundary" in tags

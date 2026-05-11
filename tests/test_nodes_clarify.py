from langgraph_agent_lab.nodes import classify_node  # import classify để test route


def test_risky_priority_over_tool() -> None:  # kiểm tra priority risky > tool
    out = classify_node({"query": "Please refund and check order status"})
    assert out["route"] == "risky"  # phải đi risky


def test_missing_info_word_boundary() -> None:  # kiểm tra word-boundary cho từ "it"
    out_vague = classify_node({"query": "Can you fix it?"})
    out_item = classify_node({"query": "Need item details"})
    assert out_vague["route"] == "missing_info"
    assert out_item["route"] != "missing_info"
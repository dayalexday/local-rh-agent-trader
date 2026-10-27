from local_rh_agent.agent import (
    extract_agentic_accounts,
    extract_tool_calls,
    order_guard,
    parse_text_tool_calls,
    stable_ref_id,
)


def test_extract_agentic_account_from_nested_payload():
    text = """
    {"accounts": [
      {"account_number": "111", "agentic_allowed": false},
      {"account_number": "9548", "agentic_allowed": true, "nickname": "MCP"}
    ]}
    """
    assert extract_agentic_accounts(text) == {"9548"}


def test_order_guard_requires_accounts_first():
    message = order_guard("place_equity_order", {"account_number": "9548"}, set())
    assert message and "get_accounts" in message


def test_order_guard_blocks_other_accounts():
    message = order_guard(
        "review_equity_order",
        {"account_number": "111", "symbol": "AAPL", "side": "buy", "type": "market"},
        {"9548"},
    )
    assert message and "not the agentic" in message
    assert order_guard(
        "place_equity_order",
        {"account_number": "9548"},
        {"9548"},
    ) is None


def test_reads_are_not_guarded():
    assert order_guard("get_portfolio", {"account_number": "111"}, {"9548"}) is None


def test_text_tool_call_fallback():
    content = '<tool_call>\n{"name": "get_accounts", "arguments": {}}\n</tool_call>'
    assert parse_text_tool_calls(content) == [("get_accounts", {})]


def test_structured_tool_calls_win():
    message = {
        "content": '<tool_call>{"name": "ignored", "arguments": {}}</tool_call>',
        "tool_calls": [{"function": {"name": "get_portfolio", "arguments": {"account_number": "9548"}}}],
    }
    assert extract_tool_calls(message) == [("get_portfolio", {"account_number": "9548"})]


def test_ref_id_is_stable_per_symbol_side_day():
    first = stable_ref_id("AAPL", "buy", "2026-11-02")
    assert first == stable_ref_id("aapl", "BUY", "2026-11-02")
    assert first != stable_ref_id("AAPL", "sell", "2026-11-02")

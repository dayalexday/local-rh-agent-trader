"""Locks the order boundary. These tests must keep failing closed."""
from datetime import date
from pathlib import Path

from local_rh_agent import config
from local_rh_agent.agent import AgentResult
from local_rh_agent.mcp_client import RobinhoodMcp
from local_rh_agent.team.executor import (
    build_execution_prompt,
    execute_with_broker,
    paper_execute,
    resolve_broker_dry_run,
)


def test_mutating_prefixes_still_cover_orders_and_not_reads():
    prefixes = config.MUTATING_PREFIXES
    assert any("place_equity_order".startswith(prefix) for prefix in prefixes)
    assert any("cancel_equity_order".startswith(prefix) for prefix in prefixes)
    assert not any("review_equity_order".startswith(prefix) for prefix in prefixes)
    assert not any("get_accounts".startswith(prefix) for prefix in prefixes)


def test_dry_run_blocks_place_and_cancel_without_a_socket(monkeypatch):
    client = RobinhoodMcp(dry_run=True)

    def boom(*args, **kwargs):
        raise AssertionError("dry-run opened a connection")

    monkeypatch.setattr(client, "_post", boom)
    placed = client.call_tool("place_equity_order", {"symbol": "AAPL", "side": "buy"})
    cancelled = client.call_tool("cancel_equity_order", {"order_id": "1"})
    assert placed["blocked"] is True
    assert placed["dry_run"] is True
    assert "not sent" in placed["message"]
    assert cancelled["blocked"] is True


def test_paper_executor_forces_dry_run_and_shows_the_account_guard(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("paper preview opened a connection")

    monkeypatch.setattr(RobinhoodMcp, "_post", boom)
    report = paper_execute({
        "orders": [{
            "symbol": "AAPL",
            "underlying": "AAPL",
            "dollar_amount": "100.00",
            "weight": 0.05,
        }]
    })
    assert report["broker_contacted"] is False
    assert report["dry_run"] is True
    row = report["orders"][0]
    assert row["dry_run_place"]["blocked"] is True
    assert row["guard_with_agentic_account"] is None
    assert "get_accounts" in row["guard_before_get_accounts"]
    assert "not the agentic" in row["guard_other_account"]


def test_env_dry_run_cannot_be_overridden(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    captured = {}

    class FakeMcp:
        def __init__(self, url=None, dry_run=None):
            captured["dry_run"] = dry_run

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    class FakeAgent:
        def __init__(self, mcp, **kwargs):
            captured["allowlist"] = kwargs.get("tool_allowlist")

        def run(self, prompt):
            captured["prompt"] = prompt
            return AgentResult(text="ok")

    monkeypatch.setattr("local_rh_agent.team.executor.RobinhoodMcp", FakeMcp)
    monkeypatch.setattr("local_rh_agent.team.executor.Agent", FakeAgent)
    monkeypatch.setattr("local_rh_agent.team.executor.config.DRY_RUN", True)
    result = execute_with_broker(
        {"orders": [], "decision": "veto"},
        day=date(2026, 11, 2),
        dry_run=False,
    )
    assert captured["dry_run"] is True
    assert result["dry_run"] is True
    assert "team_execution" not in captured["prompt"]
    assert "Do not sell and do not buy" in captured["prompt"]
    assert captured["allowlist"] == config.MONTHLY_TOOLS


def test_cli_dry_run_wins_over_a_live_environment(monkeypatch):
    monkeypatch.setattr("local_rh_agent.team.executor.config.DRY_RUN", False)
    assert resolve_broker_dry_run(True) is True
    assert resolve_broker_dry_run(False) is False


def test_execution_prompt_lists_only_the_approved_book():
    prompt = build_execution_prompt(
        {
            "orders": [{
                "symbol": "AAPU",
                "underlying": "AAPL",
                "side": "buy",
                "dollar_amount": "100.00",
                "weight": 0.05,
                "leveraged": True,
            }]
        },
        date(2026, 11, 2),
        dry_run=True,
    )
    assert "AAPU" in prompt
    assert "NVDA" not in prompt
    assert "team_execution" in prompt
    assert "monthly_rebalance" in prompt
    assert "agentic_allowed" in prompt


def test_research_modules_do_not_reference_the_broker():
    root = Path(__file__).resolve().parents[1] / "src" / "local_rh_agent" / "team"
    allowed = {"executor.py", "pipeline.py", "__init__.py"}
    for path in root.glob("*.py"):
        if path.name in allowed:
            continue
        text = path.read_text()
        assert "RobinhoodMcp" not in text, path.name
        assert "place_equity_order" not in text, path.name

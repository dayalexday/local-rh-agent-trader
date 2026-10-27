from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    env = os.getenv("LOCAL_RH_ROOT")
    if env:
        return Path(env).resolve()
    start = Path(__file__).resolve().parent
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start.parents[1]


ROOT = project_root()
load_dotenv(ROOT / ".env")

TOKENS_DIR = ROOT / ".tokens"
TOKEN_PATH = TOKENS_DIR / "rh_mcp.json"
PROMPTS_DIR = ROOT / "prompts"
RUNS_DIR = ROOT / "runs"
VEHICLES_PATH = ROOT / "vehicles.json"
SP500_CACHE = ROOT / "sp500.csv"

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# qwen2.5:3b is the default because it is already pulled and returns tool calls
# on this Mac. qwen3.5:latest is installed too, but a one-line prompt did not
# return tokens from it here. Switch OLLAMA_MODEL if you want to try it.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
RH_MCP_URL = os.getenv("RH_MCP_URL", "https://agent.robinhood.com/mcp/trading")
DRY_RUN = os.getenv("DRY_RUN", "1").strip() in {"1", "true", "True", "yes"}
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "120"))
OAUTH_PORT = int(os.getenv("RH_OAUTH_PORT", "8765"))

# Fallbacks. `oauth.discover_oauth()` prefers the live well-known metadata.
OAUTH_AUTHZ = "https://robinhood.com/oauth"
OAUTH_TOKEN = "https://api.robinhood.com/oauth2/token/"
OAUTH_REGISTER = "https://agent.robinhood.com/oauth/trading/register"
OAUTH_RESOURCE = "https://agent.robinhood.com/mcp/trading"
REDIRECT_URI = f"http://127.0.0.1:{OAUTH_PORT}/callback"
CLIENT_NAME = "local-rh-agent"

# Equity tools the monthly rebalance is allowed to see. `run` gets the full list.
MONTHLY_TOOLS = (
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_orders",
    "get_equity_quotes",
    "get_equity_tradability",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
    "search",
)

ORDER_TOOLS = frozenset({
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
})

MUTATING_PREFIXES = (
    "place_",
    "cancel_",
    "exercise_",
    "create_",
    "update_",
    "delete_",
    "add_",
    "remove_",
    "follow_",
    "unfollow_",
    "decline_",
    "mark_",
)

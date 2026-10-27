# Local RH Agent

A trading agent that runs locally.

- **LLM:** Ollama, default `qwen2.5:3b` (already pulled here; it returns tool calls)
- **Broker:** Robinhood Trading MCP at `https://agent.robinhood.com/mcp/trading`
- **Schedule:** macOS launchd, Mondays 9:45am in the Mac's timezone

This is the local counterpart of the Cursor cloud Automation in `momentum-agent`. Same monthly rule: first open Monday, rank the finished prior month, sell the agentic book, buy the top 20 equal weight, prefer a mapped 2× long single-stock ETF when Robinhood will take the order. Cursor's login is separate. This app does its own OAuth and stores tokens in `.tokens/` (mode 0600, gitignored).

Orders can only be placed in the Robinhood **Agentic** account (`agentic_allowed: true`). The client refuses order tools for every other account, on top of what Robinhood enforces.

## Layout

```
launchd job / you
        ↓
python -m local_rh_agent monthly | run "…"
        ↓
Ollama (local Qwen)  ←→  tool loop
        ↓
Robinhood Trading MCP (HTTPS + OAuth)
```

Ranking (S&P 500, prior month) is Python + Yahoo Finance, same math as `momentum-agent`. The model does not invent the top 20. It does call Robinhood: accounts, positions, tradability, review, place.

## Setup

```bash
cd ~/Documents/projects/local-rh-agent
./scripts/setup.sh
source .venv/bin/activate
```

`DRY_RUN=1` is the default. Mutating tools (`place_*`, `cancel_*`, and the other write prefixes) are dropped before the request leaves the machine. `review_*` still runs.

### Authenticate once

```bash
python -m local_rh_agent auth
```

A browser opens to Robinhood. Approve the Agentic / MCP account. Tokens land in `.tokens/rh_mcp.json`. This does not reuse the Cursor plugin session.

### Check

```bash
python -m local_rh_agent status
python -m local_rh_agent tools
python -m local_rh_agent run "List my accounts and say which one is agentic. Do not place orders."
```

### Monday job

The Mac clock should be US Eastern. The job wakes every Monday at 9:45 local time and exits immediately unless that day is the first open session of the month (Monday, or Tuesday when Monday is a holiday).

```bash
./scripts/install_launchd.sh     # enable
./scripts/uninstall_launchd.sh   # disable
```

Log: `runs/launchd.log`. Reports from a real run: `runs/YYYY-MM-DD-entry.md` and `.json` (gitignored; they contain account data).

Try the path without waiting for the first Monday, still with dry-run:

```bash
python -m local_rh_agent monthly --force
```

When you want it to send orders, set `DRY_RUN=0` in `.env`. A month that already has a live `monthly_rebalance` report will not trade again.

## Commands

| Command | What it does |
|---|---|
| `python -m local_rh_agent auth` | OAuth login, save tokens |
| `python -m local_rh_agent status` | Ollama, token, MCP reachability, whether today would trade |
| `python -m local_rh_agent tools` | Print MCP tool names |
| `python -m local_rh_agent run "…"` | One agent turn, full tool list |
| `python -m local_rh_agent rank` | Prior-month top 20, no broker |
| `python -m local_rh_agent monthly` | Calendar gate, then rank, then the model |
| `./scripts/agent_on.sh` / `agent_off.sh` | Enable / disable the Monday job |

## Models

`qwen2.5:3b` is the default. On this Mac it answers in about a second and emits real tool calls. `qwen3.5:latest` is also pulled, but a one-line prompt to it returned no tokens, so the Monday job does not use it. A 3B model will mis-fill tool arguments. Read `runs/*-transcript.json` after a `--force` dry run before you turn `DRY_RUN` off.

To try another local model:

```bash
ollama pull qwen2.5:14b
```

Then set `OLLAMA_MODEL` in `.env`. `OLLAMA_NUM_CTX` defaults to 16384 so the Robinhood tool schemas fit.

## Disclaimer

Not investment advice. Leveraged single-stock ETFs reset daily. You are responsible for every order this process places. Fund the Agentic account only with money you can lose to a bad tool call.

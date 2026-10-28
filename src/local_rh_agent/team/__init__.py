"""Research team. Order tools stay in the guarded executor, not in these agents.

Import `run_team` from `local_rh_agent.team.pipeline`. This package init stays
empty so importing a single agent does not pull in the broker client.
"""

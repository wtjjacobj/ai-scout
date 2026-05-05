"""
Hermes — the autonomous maintenance layer.

This package contains the tasks Hermes runs on a schedule to keep the AI Scout
database curated:
  - enrich.py: LLM-driven manifest generation for new/stale projects
  - (future) crawl.py, triage.py, refresh.py, prune.py, compose_brief.py

Each task is independently invocable from the CLI. The audit_log table records
every decision Hermes makes, so behavior can be inspected and tuned over time.
"""

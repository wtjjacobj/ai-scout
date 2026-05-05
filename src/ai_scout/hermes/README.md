"""
AI Scout batch enrichment runner.

Reads pending projects from DB, fetches their READMEs, outputs structured JSON
that Hermes agent can process to generate manifests.

Usage:
  # Output next N pending projects with READMEs
  python -m ai_scout.hermes.maintain batch-pending --limit N --fetch-readme

  # Write manifest for a project
  python -m ai_scout.hermes.maintain write-manifest <id> --file manifest.json

  # Check stats
  python -m ai_scout.hermes.maintain stats
"""

# The maintain.py already handles all of this. This is just documentation.

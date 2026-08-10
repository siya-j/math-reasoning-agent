#!/usr/bin/env bash
# Probe every candidate model for Lean-writing ability.
#
#   bash scripts/probe_all_models.sh
#
# Requires OPENROUTER_API_KEY and MRA_LEAN_PROJECT. Each model costs about
# eight calls; free tiers allow roughly 200 a day.
set -u

MODELS=(
  "openrouter:cohere/north-mini-code:free"
  "openrouter:poolside/laguna-s-2.1:free"
  "openrouter:poolside/laguna-xs-2.1:free"
  "openrouter:inclusionai/ling-3.0-tiny:free"
)

for model in "${MODELS[@]}"; do
  echo "=============================================="
  MRA_MODEL="$model" python scripts/probe_lean_model.py
done

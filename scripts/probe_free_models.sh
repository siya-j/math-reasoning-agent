#!/usr/bin/env bash
# Probe a list of free OpenRouter models for "can it write Lean that compiles?"
#
# WAS `modelrun.py` AT THE REPO ROOT. It is bash, not Python, and the extension
# meant every `*.py` sweep in the project tried to parse it -- line counts, any
# AST-based check, and `python -m compileall` all hit a SyntaxError on a file
# that was never Python. Nothing referenced it by name, so renaming is free.

for m in \
  "openrouter:cohere/north-mini-code:free" \
  "openrouter:poolside/laguna-s-2.1:free" \
  "openrouter:poolside/laguna-xs-2.1:free" \
  "openrouter:inclusionai/ling-3.0-tiny:free"
do
  echo "=============================================="
  MRA_MODEL="$m" python scripts/probe_lean_model.py
done

# The experimental configuration, IN THE REPOSITORY so a run can be explained.
#
#     source scripts/env.sh
#
# It lived at ~/mra-env.sh until 2026-09-21: outside version control, with a
# .bak beside it, defining the model, the backend and every budget that shapes
# a result. Nothing in the repo could say what a run was configured with, which
# is the same defect as a results row that could not reconstruct its own
# preamble -- fixed three times that day before anyone looked at the config.
#
# MACHINE-SPECIFIC PATHS below assume this WSL install. Change them here and
# the change is in the history, which is the whole point.

export PATH="$HOME/.elan/bin:$PATH"
export MRA_LEAN_PROJECT=/home/siyajethliya/lean-workspace/mathlib_playground
export MRA_PROVER=math_v2
export MRA_EXEC=local

# Switched from gemini-3.5-flash 2026-09-18. Numbers from here are not
# comparable to runs recorded under 3.5 -- the runner records the model in
# every results file, so check that field before comparing two runs.
export MRA_MODEL=google_genai:gemini-3.8-flash

# REPL backend, built 2026-09-18 from leanprover-community/repl tag v4.33.0
# with lean-toolchain overridden to v4.33.1 to match mathlib_playground.
# Subprocess pays ~39s re-importing Mathlib on EVERY compile; the REPL pays
# it once. To go back to subprocess for one run:
#     MRA_LEAN_BACKEND=subprocess python3 scripts/evaluate_proofs.py ...
export MRA_LEAN_REPL_BIN="$HOME/lean-workspace/repl/.lake/build/bin/repl"
export MRA_LEAN_BACKEND=repl

# ---------------------------------------------------------------- budgets
# PINNED, NOT INHERITED. These are the values heldout-even-63 ran under --
# they were `math_v2/core/budget.py` defaults at the time, and nothing
# recorded that. Two problems with leaving them implicit:
#
#   1. `config.py` and `math_v2/core/budget.py` carry DIFFERENT defaults for
#      the same variable names (steps 20 vs 40, lean 8 vs 12, seconds 300 vs
#      900). Which one governs depends on the prover, which is exactly the
#      confusion that produced a wrong diagnosis on 2026-09-21.
#   2. A variance measurement compares two runs and calls the difference
#      noise. That is only true if the configuration was identical, and
#      "identical" cannot mean "whatever the defaults happened to be on the
#      day".
#
# Change these deliberately and note it here, because every comparison
# against heldout-even-63 depends on them.
export MRA_MAX_AGENT_STEPS=40
export MRA_MAX_AGENT_LEAN=12
export MRA_MAX_AGENT_SEARCHES=12
export MRA_MAX_CONSECUTIVE_SEARCHES=3
export MRA_MAX_AGENT_SYMBOLIC=20
export MRA_MAX_AGENT_SECONDS=900
export MRA_MAX_MODEL_CALLS=40
export MRA_AGENT_GRACE=3
export MRA_MAX_KEPT_LEMMAS=8
export MRA_ASSEMBLE_AFTER=3
export MRA_ENGAGEMENT_FLOOR=2
export MRA_MAX_AUTO_FILLS=3
export MRA_MAX_CONTINUATIONS=2
export MRA_MAX_STATEMENT_CHECKS=4
export MRA_SEARCH_DEADLINE=0.5

# Context trimming: on, and its effect is still UNMEASURED (HANDOFF 14.4).
export MRA_CONTEXT_TRIM=24000
export MRA_CONTEXT_TRIM_KEEP=3
export MRA_CONTEXT_TRIM_INPUTS=1

# Lean timeouts can change an OUTCOME, not just robustness, so they are
# pinned too.
export MRA_LEAN_REPL_MAX_COMMANDS=2000
export MRA_LEAN_REPL_TIMEOUT=180
export MRA_LEAN_RESERVE=120

# Secrets live outside this file and outside the repo.
[ -f "$HOME/.mra-secrets" ] && . "$HOME/.mra-secrets"

if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "note: GOOGLE_API_KEY unset -- put 'export GOOGLE_API_KEY=...' in ~/.mra-secrets (chmod 600)"
fi

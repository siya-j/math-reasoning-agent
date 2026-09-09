import os

# Provider + model. Change this line, or override with MRA_MODEL, to switch
# providers. Examples: "openai:gpt-5.5", "anthropic:claude-sonnet-4-6"
MODEL = os.getenv("MRA_MODEL", "google_genai:gemini-3.5-flash")

# 0.0 = most deterministic. Good for mathematics.
TEMPERATURE = 0.0

# CEILING ON WHAT THE MODEL MAY WRITE PER CALL. Output tokens are cheap on
# their own and expensive by accumulation: the evaluation path retains the
# whole transcript, so everything the model writes is re-sent as input on
# every later call.
#
# MEASURED on eval/results/failures-after-decompose.json, output tokens per
# model call, and the separation is stark:
#
#   deep-nielsen-schreier   proved      118
#   lin-vector-space-basis  proved      234
#   hard-sum-odd-squares    proved      391   <- the most any PROOF needed
#   exercise_1_19           not_proved 2266
#   exercise_1_18a          not_formal 5413
#   exercise_3_22           refuted   12854   <- 49% of the run's entire cost
#
# Every goal that PROVED used at most 391 output tokens per call. The runaways
# are an order of magnitude above that, and reading exercise_3_22's submitted
# proof bodies shows why: paragraphs of "Wait, ... Wait, ..." deliberation
# inside the proof argument itself.
#
# 4096 is deliberately generous -- more than ten times the largest successful
# goal, and roughly 16k characters where the longest accepted proof in these
# runs is a few hundred. It bites only on pathological verbosity. It is not
# tighter because a truncated response can cut a tool call in half and turn a
# slow goal into a broken one; 2048 would separate the two groups just as
# cleanly and is one environment variable away.
#
# NOT the same thing as context trimming, which prunes old TOOL RESULTS and
# therefore cannot touch this at all -- assistant messages are not tool
# results. That is why the trim trigger of 24,000 did nothing for a goal
# running at ~175,000 input tokens per call.
MAX_OUTPUT_TOKENS = int(os.getenv("MRA_MAX_OUTPUT_TOKENS", "4096"))

# Which agent harness runs the tool-calling step: "langchain" or "deepagents".
# Kept as a switch rather than a replacement so both can be measured against
# the same 109 cases with only one variable changing.
HARNESS = os.getenv("MRA_HARNESS", "langchain")

# Deep Agents ships filesystem tools for coding agents. This agent has nine
# verification tools and no files. Leaving them on cost restraint on abstract
# claims (100% -> 92%) and produced the only soundness failure of that run, so
# they are off by default. Set MRA_DEEPAGENTS_FS=1 to measure the difference.
DEEPAGENTS_FILESYSTEM = os.getenv("MRA_DEEPAGENTS_FS", "") not in ("", "0", "false")

# Phase 4: how many verification attempts before we give up and report
# honestly. 1 disables reflection entirely.
MAX_ATTEMPTS = 3

# Phase 5: how many auxiliary claims to check when the main claim could not
# be verified. 0 disables decomposition entirely.
MAX_SUBCLAIMS = 4

# Phase 6: the Lean executable. Absent Lean is not an error — the verifier
# reports UNKNOWN, exactly as it does for a claim it cannot decide.
LEAN_COMMAND = os.getenv("MRA_LEAN", "lean")

# Path to a Lake project that depends on Mathlib. When set, proofs are checked
# with `lake env lean` from inside it, which is the ONLY way `import Mathlib`
# resolves. A bare `lean file.lean` cannot see Mathlib, however it is installed.
LEAN_PROJECT = os.getenv("MRA_LEAN_PROJECT", "")

# A proof that has not compiled in this many seconds is treated as undecided.
# Slow is not the same as false.
LEAN_TIMEOUT = int(os.getenv("MRA_LEAN_TIMEOUT", "60"))

# THE SAME QUESTION ASKED COLD, WHICH IS A DIFFERENT QUESTION. 60s above is
# tuned for the agent's own compiles, which go through a warm REPL session
# that has already paid for `import Mathlib` once. Anything using `run_lean`
# directly -- the offline verifier, `scripts/verify_results.py`,
# `tests/test_lean_real.py` -- pays that import on every call, in a fresh
# subprocess.
#
# MEASURED TWICE, on a healthy Lean 4.33.1 with a fully built Mathlib project.
# First: `import Mathlib` alone TIMED OUT at 60s, so the agent's own timeout
# was never going to serve a cold compile.
#
# Then 600s was tried, and it was still wrong -- badly enough to produce a
# FALSE FAILURE. On that machine one cold compile takes about EIGHT MINUTES
# (measured: 484s for a single test; 1703s for a run of three, one of which
# hit the ceiling). A 600s limit sits barely 25% above the real cost, so
# ordinary variance tips a healthy compile into a timeout -- and it did,
# reporting `by decide` as unverifiable when nothing was wrong with it.
#
# 1800s is three times the observed cost, which is the margin a limit needs
# when the thing it bounds varies. It is not a performance target: nothing
# should ever wait this long, and if a compile does, the timeout is a
# diagnosis rather than a budget.
#
# THE REAL LESSON IS NOT THE NUMBER. A cold subprocess compile costs minutes
# because it re-imports Mathlib every time, which is exactly why the agent
# uses a warm REPL session and sees 20-45s instead. Anything that compiles
# repeatedly through `run_lean` -- `scripts/verify_results.py` above all --
# pays that import per proof and does not scale: five proofs is forty
# minutes, a hundred is a working day. That is a design problem in the tool,
# not a number to raise here.
LEAN_COLD_TIMEOUT = int(os.getenv("MRA_LEAN_COLD_TIMEOUT", "1800"))

# --- theorem proving (Prover Agent, arXiv 2506.19923) --------------------
# Direct attempts before falling back to compiler-guided refinement.
PROOF_ATTEMPTS = 2

# Refinement rounds, each one fed the previous compiler errors.
PROOF_REFINEMENTS = 3

# How many auxiliary lemmas to propose when direct proving fails.
MAX_LEMMAS = 3

# Skeleton proving: how many `have` steps to ask for, and how many holes to
# discharge. Each hole costs at most one model call and one Lean compile, and
# the mechanical ladder is tried first, so many cost nothing.
SKELETON_STEPS = 4
MAX_HOLES = 6

# Which prover runs: "pipeline" (the measured baseline, a fixed sequence of
# stateless calls), "agentic" (one conversation with tools and persistent
# state), or "math_v2" (the blueprint agent). A switch, not a replacement.
#
# THE DEFAULT IS STILL THE BASELINE, DELIBERATELY, AND THAT IS NOT AN
# OVERSIGHT — the sentence that used to be here ("the baseline stays default
# until the comparison says otherwise") had gone stale, because the
# comparison HAS been made: every results file in `eval/results/` records
# `run.prover`, and every one of them says `math_v2`. Development moved.
#
# The default did not move with it, for two reasons that are about reading
# the record rather than about which prover is better:
#
#   1. `tests/conftest.py` clears MRA_* and reloads this module so the suite
#      runs against declared defaults. Its docstring names this exact
#      variable: a shell with `MRA_PROVER=math_v2` still exported made
#      "the baseline is the default" false and six tests failed only on that
#      machine. Flipping the default here makes that statement false
#      permanently, in the one file whose job is to keep the suite
#      independent of whoever's shell is running it.
#
#   2. Nothing depends on the default in practice. `scripts/evaluate_proofs`
#      records `run.prover` in the results file and prints it at the top of
#      the run, so a results file can never be ambiguous about which prover
#      produced it — which is the property that actually matters, and it
#      holds whichever way this line reads.
#
# So: set MRA_PROVER=math_v2 to run the current agent. That is what every
# recorded run did.
PROVER = os.getenv("MRA_PROVER", "pipeline")

# Bounds on the agentic prover. Without them a goal can loop indefinitely —
# observed on near-mathlib, where a run had to be interrupted by hand and
# produced no proof, no verdict and no record.
#
# Lean calls are budgeted separately because they are the expensive ones:
# each is roughly twenty seconds of Mathlib loading.
#
# THE NAMES ARE `MRA_AGENTIC_*`, AND THE `MRA_MAX_AGENT_*` FALLBACK IS
# COMPATIBILITY, NOT THE INTENT. These four used to read the bare
# `MRA_MAX_AGENT_*` names — which `math_v2/core/budget.py` ALSO reads, with
# DIFFERENT defaults, because the two provers want different bounds:
#
#     env var                   agentic (here)   math_v2 (budget.py)
#     MRA_MAX_AGENT_STEPS            20                  40
#     MRA_MAX_AGENT_LEAN              8                  12
#     MRA_MAX_AGENT_SEARCHES          8                  12
#     MRA_MAX_AGENT_SECONDS         300                 900
#
# Only one prover runs per process, so this was never a live miscount. It was
# a reading hazard, and it had already misled the codebase in writing:
# `tests/test_budget_profile_flag.py` states that those names are "math_v2's
# own env var names -- no other configured prover reads them", which was not
# true while these lines read them. Distinct names make that claim true.
#
# The fallback is kept because exporting `MRA_MAX_AGENT_LEAN` to bound an
# agentic run is a thing that has been done, and silently ignoring it would
# be a worse failure than the ambiguity being fixed. `--budget-profile` is
# not affected either way: `pipeline.proving.budget_profile` returns {}
# unless the math_v2 prover is selected, so it has never set these.
MAX_AGENT_STEPS = int(
    os.getenv("MRA_AGENTIC_STEPS") or os.getenv("MRA_MAX_AGENT_STEPS", "20"))
MAX_AGENT_LEAN_CALLS = int(
    os.getenv("MRA_AGENTIC_LEAN") or os.getenv("MRA_MAX_AGENT_LEAN", "8"))
MAX_AGENT_SECONDS = float(
    os.getenv("MRA_AGENTIC_SECONDS")
    or os.getenv("MRA_MAX_AGENT_SECONDS", "300"))

# Search needs its own ceiling, not just a share of the total. Measured on
# near-mathlib: `num-primes-strictly-above` spent all 20 tool calls on search
# and never compiled once. The run terminated correctly and proved nothing —
# the bound worked, the allocation did not.
MAX_AGENT_SEARCHES = int(
    os.getenv("MRA_AGENTIC_SEARCHES")
    or os.getenv("MRA_MAX_AGENT_SEARCHES", "8"))

# Auxiliary lemmas the agent may keep. Each one is a Lean compile and grows
# the file every later attempt must recompile, so this is not free.
MAX_AGENT_LEMMAS = int(os.getenv("MRA_MAX_AGENT_LEMMAS", "4"))

# Searches allowed back-to-back before the agent must compile something. A
# rejected compile returns the goal state, which is worth more than another
# query; searching is also the cheap action, so an uncertain agent will prefer
# it indefinitely. This is a nudge enforced in code, not asked for in prose.
MAX_CONSECUTIVE_SEARCHES = int(os.getenv("MRA_MAX_CONSECUTIVE_SEARCHES", "3"))

# Compile the STATEMENT with `sorry` before proving it, and repair it once if
# Lean cannot make sense of it. Measured on lin-vector-space-basis: the
# formalizer wrote `Basis`, which current Mathlib calls `Module.Basis`, so no
# proof could ever have compiled — and the run still reported a formalisation
# rate of 100%. Costs one Lean invocation per goal.
CHECK_STATEMENT = os.getenv("MRA_CHECK_STATEMENT", "1") not in ("0", "false", "")

# Repair attempts for a statement Lean rejects. More than one because Lean
# reports what stopped it, not everything wrong: an outdated name AND an
# undeclared universe are two faults, and fixing the first only reveals the
# second. Each attempt sees every earlier one, or it just repeats itself.
MAX_STATEMENT_REPAIRS = int(os.getenv("MRA_MAX_STATEMENT_REPAIRS", "2"))

# Retrieval can be turned off to attribute its contribution in an ablation.
RETRIEVAL_ENABLED = os.getenv("MRA_RETRIEVAL", "1") not in ("0", "false", "")

# How deep lemma generation may recurse. 1 means lemmas are proved directly
# and never decomposed further. Raise with care: cost grows multiplicatively.
LEMMA_DEPTH = 1

# --- premise retrieval (LeanDojo, arXiv 2306.15626) ----------------------
# Mathlib search over HTTP. Needs no Lean installation and no API key.
LOOGLE_URL = os.getenv("MRA_LOOGLE_URL", "https://loogle.lean-lang.org/json")
LOOGLE_TIMEOUT = int(os.getenv("MRA_LOOGLE_TIMEOUT", "10"))

# Results kept per query, how many identifiers to look up, and the total
# shown to the model.
#
# PREMISES_PER_QUERY was 6, which was measured to be far too few: Loogle
# ranks by module order rather than relevance, so a correct query can still
# bury the needed lemma. On the infinitude-of-primes goal the model was shown
# "11 is prime, 5 is prime, 7 is prime" and nothing else.
PREMISES_PER_QUERY = 12
MAX_PREMISE_QUERIES = 4

# Total premises put in front of the model. A prompt stuffed with premises is
# as unhelpful as an empty one, so the ladder in premises_for() stops here.
PREMISE_BUDGET = 24

# How many retrieved premises the deterministic tactic attempt tries. Every
# one adds alternatives to a single `first | ...` block, so the cost is file
# size rather than extra Lean invocations.
CHEAP_PREMISES = 12

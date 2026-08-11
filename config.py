import os

# Provider + model. Change this line, or override with MRA_MODEL, to switch
# providers. Examples: "openai:gpt-5.5", "anthropic:claude-sonnet-4-6"
MODEL = os.getenv("MRA_MODEL", "google_genai:gemini-3.5-flash")

# 0.0 = most deterministic. Good for mathematics.
TEMPERATURE = 0.0

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
# stateless calls) or "agentic" (one conversation with tools and persistent
# state). A switch, not a replacement — the baseline stays default until the
# comparison says otherwise.
PROVER = os.getenv("MRA_PROVER", "pipeline")

# Bounds on the agentic prover. Without them a goal can loop indefinitely —
# observed on near-mathlib, where a run had to be interrupted by hand and
# produced no proof, no verdict and no record.
#
# Lean calls are budgeted separately because they are the expensive ones:
# each is roughly twenty seconds of Mathlib loading.
MAX_AGENT_STEPS = int(os.getenv("MRA_MAX_AGENT_STEPS", "20"))
MAX_AGENT_LEAN_CALLS = int(os.getenv("MRA_MAX_AGENT_LEAN", "8"))
MAX_AGENT_SECONDS = float(os.getenv("MRA_MAX_AGENT_SECONDS", "300"))

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

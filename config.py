import os

# Provider + model. Change this line, or override with MRA_MODEL, to switch
# providers. Examples: "openai:gpt-5.5", "anthropic:claude-sonnet-4-6"
MODEL = os.getenv("MRA_MODEL", "google_genai:gemini-3.5-flash")

# 0.0 = most deterministic. Good for mathematics.
TEMPERATURE = 0.0

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

# How deep lemma generation may recurse. 1 means lemmas are proved directly
# and never decomposed further. Raise with care: cost grows multiplicatively.
LEMMA_DEPTH = 1

# --- premise retrieval (LeanDojo, arXiv 2306.15626) ----------------------
# Mathlib search over HTTP. Needs no Lean installation and no API key.
LOOGLE_URL = os.getenv("MRA_LOOGLE_URL", "https://loogle.lean-lang.org/json")
LOOGLE_TIMEOUT = int(os.getenv("MRA_LOOGLE_TIMEOUT", "10"))

# Results kept per query, and how many identifiers from a statement to look
# up. Both small: a prompt full of premises is as unhelpful as an empty one.
PREMISES_PER_QUERY = 6
MAX_PREMISE_QUERIES = 4

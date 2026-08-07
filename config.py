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

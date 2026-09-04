"""Math v2 agent factory — a Deep Agent for symbolic and formal mathematics.

Follows the canonical skeleton in AGENT_BLUEPRINT.md §3. `from __future__
import annotations` is permitted here (§5.1) — this is not a tool module.

THE ONE THING TO PRESERVE
-------------------------
`finish` is in the tool list and it is NOT optional for this agent. §5.5 calls
it framework-optional because a deep agent terminates fine when the model stops
calling tools. That is true and, here, beside the point: a natural stop lets
the model end a turn asserting a proof that never compiled. `finish` computes
the verdict from recorded compilations and refuses a claim no record supports.
Remove it and this stops being a verifier.

WHY THE BUDGET IS NOT MIDDLEWARE (YET)
--------------------------------------
`lean_common.LiteratureSearchCapMiddleware` is the house pattern for a
guaranteed-termination call cap, and the right long-term home for ours. Its
stop mechanism could not be verified — error `ToolMessage`, raise, or
`jump_to` — and a bound whose mechanism we are guessing at is not a bound. The
measured failure this protects against is real: an agent loop that had to be
interrupted by hand, leaving no proof, no verdict and no record. Until the
mechanism is confirmed, `tool_budget=` below carries the framework's own cap.

VERIFIED / UNVERIFIED
---------------------
Verified against the blueprint's §3 skeleton, which it states `chem_v2` follows
verbatim, and against §8's quoted supervisor call site: the parameter names
below are keyword-only and exact, and renaming one is a `TypeError` at
supervisor build. Unverified: the exact defaults of `make_agent_middleware` and
`make_lean_backend`, which is why only documented keywords are passed.
"""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from ..lean_common import make_agent_middleware, make_lean_backend
from .context import MathContext
from .prompt import system_prompt
from .tools import create_math_v2_tools

# A 2-TUPLE, `(soft, hard)`, because that is what the middleware unpacks:
# `make_agent_middleware` does `TurnToolBudgetMiddleware(*tool_budget)` and the
# constructor is `__init__(self, soft, hard)`. This was a bare `40`, so
# `create_math_v2_agent()` raised `TypeError: argument after * must be an
# iterable, not int` and COULD NOT BUILD AT ALL. It went unnoticed because the
# evaluation path does not use this constructor -- `harness.build_agent` calls
# LangChain's `create_agent` directly -- so every number this project has ever
# produced came from a path that never touches this file. The Aura integration
# would have hit it on first use. Every other caller passes a tuple
# (`research/agent.py` uses `(4, 7)`).
#
# DELIBERATELY WELL ABOVE math_v2's OWN BUDGET, which is the part worth
# thinking about rather than copying. `core/budget.py` already bounds a run:
# MAX_TOOL_CALLS is 40 by default and 120 under `--budget-profile
# hard-reasoning`. If these numbers sat at or below those, this middleware
# would become the binding constraint INVISIBLY -- a run would stop without
# `budget.summary()` recording a reason, so `eval.proof_metrics.classify`
# would read it as NOT_PROVED rather than EXHAUSTED and score a budget failure
# as a proving failure. Two budgets are already one more than ideal; the outer
# one must never be the one that bites. So this is a runaway guard only, and
# math_v2's own budget stays the thing that stops a run and says why.
#
# soft nudges the model to wrap up; hard blocks further tool calls, except for
# `finish`, which the middleware always allows -- so a run that hits the hard
# stop can still report its outcome rather than dying silently.
TOOL_BUDGET = (150, 200)


def create_math_v2_agent(
    *,
    model: str | BaseChatModel = "google_genai:gemini-3-flash-preview",
    workspace_path: str,
    checkpointer=None,
    context_schema=None,      # SessionFactory passes AuraContext to unify the tree
    skills_middleware=None,   # per-user Studio skills, injected at build time
    extra_tools: list | None = None,   # per-user toolkit (MCP) tools
):
    tools = create_math_v2_tools() + list(extra_tools or [])
    backend = make_lean_backend(workspace_path=workspace_path, runtime="math")
    if checkpointer is None:
        checkpointer = MemorySaver()

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt(),
        backend=backend,
        checkpointer=checkpointer,
        context_schema=context_schema or MathContext,
        middleware=make_agent_middleware(
            model,
            backend,
            # A leaf spoke, so self-validation applies: on a no-tool natural
            # stop it loops the model back once to confirm it did not fabricate
            # a result it only described. That is a second line of defence
            # behind `finish`, not a replacement for it.
            self_validate=True,
            skills_middleware=skills_middleware,
            tool_budget=TOOL_BUDGET,
        ),
        name="math",
    )


# The routing description the supervisor sees (§8). Names concrete verbs, and
# states the boundary — without the last sentence the supervisor will merge
# this agent with whichever one owns numerical work.
FACTORY_DESCRIPTION = (
    "Symbolic and formal mathematics: evaluates and checks algebraic "
    "identities, derivatives, integrals, limits, series, matrices, "
    "inequalities, primality and factorisations with a computer algebra "
    "system; searches Mathlib for existing theorems; and writes and "
    "machine-checks Lean 4 proofs, reporting a claim as proved only when the "
    "compiler accepts it. Use for 'prove', 'verify', 'is this identity true', "
    "'solve', 'simplify'. Not for numerical simulation, data fitting or "
    "statistical modelling."
)

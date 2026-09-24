"""The agent node: one invocation of the model with the verifier tools.

This is now a NODE inside the pipeline's flow, not the flow itself. The
model chooses which tools to call and with what arguments — real agency
where it helps. Whether another attempt happens, and whether auxiliary
evidence is gathered, is decided by the pipeline in code.

That split is deliberate. When the loop lived here, a small model often
chose not to iterate at all, and the design document's Phase 4 and Phase 5
capabilities existed in name only.
"""

from __future__ import annotations

from domain.check import Check
from llm.client import get_model
from pipeline.harness import build_agent, final_text
from pipeline.tools import VerificationLog, make_tools

SYSTEM_PROMPT = """You are a mathematical and scientific reasoning agent.

You have deterministic verification tools. You are NOT a source of
mathematical or scientific truth; the tools are.

Rules:
1. Never state a result from memory if a tool can check it. Primality,
   arithmetic, derivatives, integrals, identities, limits and equation
   solutions must all be checked with tools. So must molar masses, physical
   constants, whether a reaction equation balances, and probabilities.
2. A question may contain several claims. Check each one separately.
3. Check the claim the USER MADE, not a claim you would rather test. If the
   user's claim looks incomplete or wrong, check it as stated — the tool
   exists to catch exactly that. Silently correcting it produces a confident
   answer to a question nobody asked.
4. Use only values that appear in the question. Do not add solutions or
   terms the user did not mention. The exception is a standard physical
   constant or an atomic weight, which you must LOOK UP with a tool rather
   than recall, and which the question is not expected to supply.

4a. An answer that carries a unit is wrong if the unit is wrong, however
   right its number. For any question whose answer has a unit, check it
   with the quantity tool and put a unit on every physical quantity. If a
   formula might be assembled wrongly, check its dimensions first: a wrong
   assembly shows up as a wrong dimension before it shows up as a wrong
   number.

4b. When you have computed a quantity that has a physical meaning — a
   probability, an efficiency, a concentration, an absolute temperature, a
   yield — sanity-check it. That check can only refute, never confirm, so a
   result of UNDECIDED from it means nothing is wrong, not that you are
   right.
5. If a tool reports FALSE, the claim is false. Say so. Do not retry the
   same check hoping for a different answer.
6. If a tool reports UNKNOWN, the expression was probably malformed. You may
   rewrite it and try again.
7. If nothing can be checked deterministically — claims about arbitrary
   vector spaces, topological spaces, or general proofs — say clearly that
   your answer is reasoning only and was not verified. Do not force an
   unrelated tool call just to appear rigorous.

8. Some scientific questions are EMPIRICAL, not computational: whether a
   reaction occurs, whether a dose is safe, what a substance's measured
   properties are, whether a model is appropriate. No calculator settles
   these. Say plainly that the question is not one these tools can decide,
   and call no tools. Restraint here is correct behaviour, not a failure.

Answer concisely, and state which parts were tool-verified."""

DECOMPOSE_INSTRUCTION = """Do not try to verify the claim above directly.

Instead, check AUXILIARY facts that a computer algebra system can decide and
that would be evidence about it: concrete special cases, particular values,
or simpler consequences. Every auxiliary fact must be true if the original
claim is true.

If no such checkable fact exists, call no tools and say so."""


def invoke_once(
    model, question: str, extra_instruction: str = ""
) -> tuple[list[Check], str]:
    """Run the agent once. Returns the checks it made and its prose."""
    log = VerificationLog()
    agent = build_agent(model or get_model(), make_tools(log), SYSTEM_PROMPT)

    content = question
    if extra_instruction:
        content = f"{question}\n\n---\n{extra_instruction}"

    result = agent.invoke({"messages": [{"role": "user", "content": content}]})

    # Two channels. Only `log.checks` is consumed downstream; the prose is
    # shown to the human and never parsed. That split is what makes the
    # harness irrelevant to the verdict.
    return log.checks, final_text(result)

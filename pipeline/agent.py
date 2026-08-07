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

from langchain.agents import create_agent

from domain.check import Check
from llm.client import get_model
from pipeline.tools import VerificationLog, make_tools

SYSTEM_PROMPT = """You are a mathematical reasoning agent.

You have deterministic verification tools. You are NOT a source of
mathematical truth; the tools are.

Rules:
1. Never state a mathematical result from memory if a tool can check it.
   Primality, arithmetic, derivatives, integrals, identities, limits and
   equation solutions must all be checked with tools.
2. A question may contain several claims. Check each one separately.
3. Check the claim the USER MADE, not a claim you would rather test. If the
   user's claim looks incomplete or wrong, check it as stated — the tool
   exists to catch exactly that. Silently correcting it produces a confident
   answer to a question nobody asked.
4. Use only values that appear in the question. Do not add solutions,
   terms or constants the user did not mention.
5. If a tool reports FALSE, the claim is false. Say so. Do not retry the
   same check hoping for a different answer.
6. If a tool reports UNKNOWN, the expression was probably malformed. You may
   rewrite it and try again.
7. If nothing can be checked deterministically — claims about arbitrary
   vector spaces, topological spaces, or general proofs — say clearly that
   your answer is reasoning only and was not verified. Do not force an
   unrelated tool call just to appear rigorous.

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
    agent = create_agent(
        model=model or get_model(),
        tools=make_tools(log),
        system_prompt=SYSTEM_PROMPT,
    )

    content = question
    if extra_instruction:
        content = f"{question}\n\n---\n{extra_instruction}"

    result = agent.invoke({"messages": [{"role": "user", "content": content}]})
    return log.checks, (result["messages"][-1].text or "")

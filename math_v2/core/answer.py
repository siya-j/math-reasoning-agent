"""The final answer, rebuilt from `finish`'s own reply when the model cannot write it.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

THE FAILURE THIS EXISTS FOR
---------------------------
MEASURED through Aura, 2026-09-27: "Verify that ψ_n(x) = √(2/L)·sin(nπx/L) is
normalized on [0, L]" was PROVED -- `finish` accepted a compiled proof -- and
then the 40th model call was spent before the agent wrote its answer. The run's
final message was the call-limit notice, and the supervisor told the user the
specialist had "halted before returning a proof". A verified result was
reported as a failure.

`finish` is where the result is decided, and its reply already carries
everything an answer states: the outcome, the compiled statement and proof (or
refutation), the lemmas the proof cites with their recorded statements, and
the file. So when a run stops after an ACCEPTED finish, the answer is written
from that reply, deterministically, with no model call and nothing added that
the record does not hold. The agent's own `summary` stands in for the
reasoning, labelled as its summary.
"""

import ast
import json

NOT_RECORDED = "statement not recorded"


def _content_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content or ""


def _parse(text):
    for parse in (json.loads, ast.literal_eval):
        try:
            value = parse(text)
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def accepted_finish(messages):
    """(payload, args) of the last ACCEPTED `finish` in `messages`, or (None, None).

    `args` are the arguments the model called it with -- `summary`, `claim`,
    `statement` -- read from the AIMessage whose tool call the reply answers.
    """
    calls = {}
    for message in messages or []:
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == "finish":
                calls[call.get("id")] = call.get("args") or {}
    for message in reversed(messages or []):
        if getattr(message, "type", None) != "tool" or getattr(message, "name", None) != "finish":
            continue
        payload = _parse(_content_text(message))
        if payload and payload.get("accepted"):
            return payload, calls.get(getattr(message, "tool_call_id", None), {})
    return None, None


def _lean_block(statement, proof):
    statement, proof = (statement or "").strip(), (proof or "").strip()
    body = proof if proof.startswith((":=", "by")) else f"by\n  {proof}"
    joiner = " " if body.startswith(":=") else " := "
    return f"```lean\n{statement}{joiner}{body}\n```"


def render(payload, args):
    """The answer `finish`'s reply supports, in the same order the agent writes one."""
    args = args or {}
    evidence = payload.get("evidence") or {}
    refutation = evidence.get("refutation") or {}
    summary = (args.get("summary") or payload.get("summary") or "").strip()
    claim = (args.get("claim") or "").strip()
    banner = payload.get("banner") or payload.get("outcome", "")
    parts = []

    if evidence.get("proof") or refutation.get("proof"):
        if claim:
            parts.append(f"**The claim.** {claim}")
        if summary:
            parts.append(f"**The reasoning** (the agent's summary). {summary}")
        if evidence.get("proof"):
            parts.append("**The Lean proof.**\n\n" + _lean_block(evidence.get("statement"), evidence["proof"]))
        else:
            parts.append("**The Lean proof** (of the negation: the claim is false).\n\n"
                         + _lean_block(refutation.get("statement"), refutation["proof"]))
        lemmas = payload.get("lemmas_used") or []
        if lemmas:
            lines = [f"- `{entry['name']}`: " + (f"`{entry['statement']}`" if entry.get("statement") else NOT_RECORDED)
                     for entry in lemmas]
            parts.append("**Lemmas used.**\n" + "\n".join(lines))
        else:
            parts.append("**Lemmas used.** The proof cites no lemma by name; its tactics close the goal.")
    else:
        # A computation: the result first, as the calculation layout puts it.
        if summary:
            parts.append(f"**Result** (the agent's summary). {summary}")

    parts.append(f"**Verification status.** {banner}.")
    files = [f for f in (payload.get("lean_file"), "math/proof_log.json") if f]
    parts.append("**Files.** " + ", ".join(f"`{f}`" for f in files))
    parts.append("_Written from the verified record: the run reached its model-call limit after "
                 "`finish` accepted this result and before the agent wrote its own answer._")
    return "\n\n".join(parts)

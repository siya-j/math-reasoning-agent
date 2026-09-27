"""A result `finish` accepted survives the model-call cap. Offline.

MEASURED through Aura, 2026-09-27: "Verify that ψ_n(x) = √(2/L)·sin(nπx/L) is
normalized on [0, L]" was proved -- `finish` accepted a compiled proof -- and
the 40th model call went before the agent wrote its answer. The final message
was "Model call limit reached: 40/40 ...", and the user was told the specialist
had failed. These tests put the limiter in exactly that state.
"""

import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from math_v2 import call_limit
from math_v2.context import MathContext
from math_v2.core import budget, log
from math_v2.tools.control import finish

STATEMENT = ("theorem wave_function_normalized (n : ℕ) (hn : 1 ≤ n) (L : ℝ) (hL : 0 < L) :\n"
             "  ∫ x in (0 : ℝ)..L, (Real.sqrt (2 / L) * Real.sin (n * Real.pi * x / L)) ^ 2 = 1")
PROOF = "by\n  have h := key n hn L hL\n  simpa using h"
CLAIM = ("Verify that ψ_n(x) = √(2/L)·sin(nπx/L) is normalized on [0, L], i.e. that the integral of "
         "ψ_n² over [0, L] is 1, for every integer n ≥ 1.")


def _runtime(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)), config={},
                       stream_writer=lambda *a, **k: None, tool_call_id="f1", store=None)


def _finish(workdir, **args):
    return asyncio.run(finish.ainvoke({**args, "runtime": _runtime(workdir)}))


def _state_after(workdir, args, payload):
    return {"messages": [
        HumanMessage(CLAIM),
        AIMessage("", tool_calls=[{"name": "finish", "args": args, "id": "f1"}]),
        ToolMessage(content=json.dumps(payload), name="finish", tool_call_id="f1"),
    ]}


def _at_the_cap(workdir, limit=40):
    budget.reset(str(workdir))
    for _ in range(limit):
        budget.charge_model_call(str(workdir))
    return call_limit.limiter(limit), SimpleNamespace(context=SimpleNamespace(workdir=str(workdir)))


def test_the_p2_situation_answers_from_the_accepted_proof(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT, proof=PROOF, status=log.TRUE))
    args = {"summary": "Using sin² = (1 − cos 2θ)/2 the integral is (2/L)(L/2) = 1.",
            "outcome": "proved", "statement": STATEMENT, "claim": CLAIM}
    payload = _finish(tmp_path, **args)
    assert payload["accepted"] is True

    limiter, runtime = _at_the_cap(tmp_path)
    update = limiter.before_model(_state_after(tmp_path, args, payload), runtime)

    assert update["jump_to"] == "end"
    [message] = update["messages"]
    text = message.content
    assert not text.startswith("Model call limit reached")
    for piece in ("**The claim.**", CLAIM, "**The Lean proof.**", "```lean", "simpa using h",
                  "**Verification status.**", "`math/proof.lean`", "Written from the verified record"):
        assert piece in text, piece
    assert message.response_metadata.get("mra_answer_from_finish") is True


def test_lemmas_keep_their_recorded_statement_or_say_not_recorded(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT, proof=PROOF, status=log.TRUE))
    args = {"summary": "s", "outcome": "proved", "statement": STATEMENT, "claim": CLAIM}
    payload = _finish(tmp_path, **args)
    payload["lemmas_used"] = [
        {"name": "intervalIntegral.integral_comp_mul_left", "statement": "∫ x in a..b, f (c * x) = c⁻¹ • ∫ x in c*a..c*b, f x"},
        {"name": "Real.pi_ne_zero", "statement": ""},
    ]
    limiter, runtime = _at_the_cap(tmp_path)
    text = limiter.before_model(_state_after(tmp_path, args, payload), runtime)["messages"][0].content

    assert "`intervalIntegral.integral_comp_mul_left`: `∫ x in a..b" in text
    assert "`Real.pi_ne_zero`: statement not recorded" in text


def test_without_an_accepted_finish_the_notice_is_unchanged(tmp_path):
    limiter, runtime = _at_the_cap(tmp_path)
    text = limiter.before_model({"messages": [HumanMessage(CLAIM)]}, runtime)["messages"][0].content
    assert text.startswith("Model call limit reached: 40/40")


def test_a_refused_finish_does_not_count(tmp_path):
    args = {"summary": "s", "outcome": "proved", "statement": STATEMENT, "claim": CLAIM}
    payload = _finish(tmp_path, **args)              # nothing compiled: refused
    assert payload["accepted"] is False
    limiter, runtime = _at_the_cap(tmp_path)
    text = limiter.before_model(_state_after(tmp_path, args, payload), runtime)["messages"][0].content
    assert text.startswith("Model call limit reached")


def test_a_refutation_is_shown_as_the_proof_of_the_negation(tmp_path):
    goal = "theorem euler (n : ℕ) : Nat.Prime (n ^ 2 + n + 41)"
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=goal, proof="by aesop", status=log.FALSE))
    log.append(str(tmp_path), log.Record(kind=log.REFUTATION,
                                         statement="theorem euler_refutation : ¬ (∀ n : ℕ, Nat.Prime (n ^ 2 + n + 41))",
                                         proof="intro h\n  exact absurd (h 40) (by norm_num)", status=log.TRUE))
    args = {"summary": "false at n = 40", "outcome": "statement_suspect", "claim": "Is n²+n+41 always prime?"}
    payload = _finish(tmp_path, **args)
    assert payload["outcome"] == "refuted"

    limiter, runtime = _at_the_cap(tmp_path)
    text = limiter.before_model(_state_after(tmp_path, args, payload), runtime)["messages"][0].content
    assert "of the negation: the claim is false" in text
    assert "exact absurd (h 40)" in text and "`math/refutation.lean`" in text


def test_a_computation_leads_with_the_result(tmp_path):
    log.append(str(tmp_path), log.Record(kind="computation", statement="check_numeric(...)", status=log.TRUE,
                                         detail="1.91"))
    args = {"summary": "k(310)/k(300) = 1.91", "outcome": "not_formalized", "claim": "How much faster?"}
    payload = _finish(tmp_path, **args)
    assert payload["accepted"] is True

    limiter, runtime = _at_the_cap(tmp_path)
    text = limiter.before_model(_state_after(tmp_path, args, payload), runtime)["messages"][0].content
    assert text.startswith("**Result**") and "1.91" in text
    assert "```lean" not in text and "Lemmas used" not in text

"""Would a Lean-specialised prover close the goals this agent could not?

THE QUESTION, AND WHY THE DATA POINTS AT IT

Two independent measurements over eval/evidence/ say the bottleneck is tactic
generation rather than information:

  * 31% of every failure is a NAME OR TYPE error -- `Application type
    mismatch`, `failed to synthesize instance`, `Unknown identifier` -- and
    the agent HAS the full signature when it makes them. All 5,245 remembered
    premises carry their type and docstring. It sees
    `Fin.append_elim0 {m : ℕ} ... : ...` and still applies it wrong.
  * Another 29% is a bare `unsolved goals`: the tactic simply did not close
    it. That is precisely what a tactic-trained model is for.

And retrieval, the other candidate, is measurably NOT the lever: it supplies
34% of the identifiers accepted proofs cite while consuming 46% of all model
turns.

WHAT THIS SPIKE IS, AND IS NOT

It is NOT an integration. `llm.get_model` builds one model and the tool layer
calls it; wiring a second in properly means a second client and a routing
tool. This is a standalone experiment that answers "would it help" BEFORE any
of that is built.

It costs NO API tokens. The corpus comes from workspaces already on disk, and
the prover is served locally.

THE STANDARD IS UNCHANGED, deliberately. A proof from the prover model goes
through the SAME `verifiers.lean_verifier.interpret` as the agent's own --
which refuses `sorry`, `axiom`, `native_decide` and suggestion tactics. A
model that scores well by cheating scores zero here, exactly as the agent
would.

    python scripts/prover_spike.py --corpus            # free, no model
    python scripts/prover_spike.py --run --url http://localhost:8000/v1
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "eval" / "prover-spike-corpus.json"

ASK = """Prove this Lean 4 theorem using Mathlib.

{statement}

Reply with ONLY the proof term or tactic block that follows `:=`. No
explanation, no code fence, no restatement of the theorem. Do not use `sorry`,
`admit`, `native_decide`, or `exact?`/`apply?` -- they are rejected."""


def build_corpus() -> list:
    """Every EXTERNAL goal this agent has failed to prove, with its statement.

    Taken from the results files rather than the workspaces, because a
    results file records the outcome the guard actually reached. Only
    `not_proved` -- a refuted or suspect statement is not a proving failure
    and handing it to another prover would measure nothing.
    """
    latest = {}
    for path in sorted(glob.glob(str(ROOT / "eval" / "results" / "*.json")),
                       key=os.path.getmtime):
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))["results"]
        except (ValueError, OSError, KeyError):
            continue
        for row in rows:
            if row.get("tier") in ("proofnet", "putnam") and row.get("outcome") != "error":
                latest[row["goal_id"]] = row

    corpus = []
    for goal_id, row in sorted(latest.items()):
        if row["outcome"] != "not_proved":
            continue
        statement = (row.get("statement") or "").strip()
        if not statement:
            continue
        corpus.append({
            "goal_id": goal_id,
            "tier": row["tier"],
            "statement": statement,
            # What OUR agent spent failing, so the comparison has a baseline.
            "agent_goal_attempts": row.get("goal_attempts", 0),
            "agent_lean_calls": row.get("lean_calls", 0),
            "agent_input_tokens": row.get("input_tokens", 0),
            # Lemmas it kept are prepended, exactly as the agent compiles them.
            "lemmas": list(row.get("lemmas") or []),
        })
    return corpus


async def _compile(statement: str, proof: str, lemmas: list):
    from math_v2.tools._util import lean_runner
    from verifiers.lean_verifier import build_source, interpret

    combined = "\n\n".join(list(lemmas) + [statement]) if lemmas else statement
    result = await lean_runner(".")(build_source(combined, proof))
    return result, interpret(result, statement)


def _ask_local(url: str, model: str, statement: str, timeout: float) -> str:
    """One completion from an OpenAI-compatible local server (vLLM, llama.cpp).

    Kept to the standard library: this must run on a machine that has a GPU
    and a served model, not one that has this project's dependency tree.
    """
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": ASK.format(statement=statement)}],
        "temperature": 0.0,
        "max_tokens": 2048,
    }).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/chat/completions", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    text = body["choices"][0]["message"]["content"]
    # Strip a code fence if the model added one despite being asked not to.
    if "```" in text:
        parts = [p for p in text.split("```") if p.strip()]
        text = max(parts, key=len)
        if text.lstrip().startswith("lean"):
            text = text.lstrip()[4:]
    return text.strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", action="store_true",
                        help="build and describe the corpus. Costs nothing.")
    parser.add_argument("--run", action="store_true",
                        help="actually query a served prover and compile")
    parser.add_argument("--url", default="http://localhost:8000/v1")
    parser.add_argument("--model", default="Goedel-LM/Goedel-Prover-V2-8B")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    corpus = build_corpus()
    CORPUS.write_text(json.dumps(corpus, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"corpus: {len(corpus)} external goals this agent failed to prove")
    print(f"written to {CORPUS.relative_to(ROOT)}\n")

    spent = sum(g["agent_input_tokens"] for g in corpus)
    print(f"{'goal':22} {'tier':9} {'attempts':>8} {'compiles':>9} {'our cost':>12}")
    for g in corpus:
        print(f"  {g['goal_id']:20} {g['tier']:9} {g['agent_goal_attempts']:>8} "
              f"{g['agent_lean_calls']:>9} {g['agent_input_tokens']:>12,}")
    print(f"\n  this agent spent {spent:,} input tokens failing these.")

    if not args.run:
        print()
        print("DRY RUN. Nothing was queried and nothing was compiled.")
        print()
        print("To run it you need a served prover, for example:")
        print("  pip install vllm")
        print(f"  vllm serve {args.model} --port 8000 --max-model-len 8192")
        print("then:")
        print("  python scripts/prover_spike.py --run")
        print()
        print("Every proof it returns is compiled through the SAME")
        print("`lean_verifier.interpret` the agent uses, so `sorry`, `axiom`,")
        print("`native_decide` and suggestion tactics are refused identically.")
        return 0

    goals = corpus[: args.limit] if args.limit else corpus
    closed = []
    for index, goal in enumerate(goals, 1):
        try:
            proof = _ask_local(args.url, args.model, goal["statement"], args.timeout)
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the sweep
            print(f"[{index}/{len(goals)}] {goal['goal_id']:22} SERVER {type(exc).__name__}")
            continue
        result, verdict = asyncio.run(
            _compile(goal["statement"], proof, goal["lemmas"]))
        from domain.verdict import VerificationStatus
        ok = verdict.status is VerificationStatus.TRUE
        closed.append((goal["goal_id"], ok, proof))
        print(f"[{index}/{len(goals)}] {goal['goal_id']:22} "
              f"{'PROVED' if ok else result.outcome.value}")

    won = [g for g, ok, _ in closed if ok]
    print()
    print("=" * 66)
    print(f"  the prover closed {len(won)} of {len(closed)} goals this agent could not")
    for g in won:
        print(f"    {g}")
    print()
    print("  A goal closed here is a goal the CURRENT architecture cannot")
    print("  reach and a specialised prover can -- which is the only thing")
    print("  this experiment is entitled to conclude. It says nothing about")
    print("  what the two would do together, and nothing about goals the")
    print("  agent already proves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

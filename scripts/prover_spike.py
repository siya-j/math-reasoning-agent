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
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "eval" / "prover-spike-corpus.json"
# GENERATED PROOFS ARE KEPT, because generating them is the expensive half.
# MEASURED: three proofs at ~4 minutes each were produced and then discarded
# when the compile step found no Lean. On this hardware a generation costs
# minutes and a compile costs seconds, so throwing away the minutes to redo
# the seconds is the wrong way round. Cached by goal AND model, so serving a
# different prover does not silently reuse the last one's answers.
CACHE = ROOT / "eval" / "prover-spike-generations.json"

# ASKED IN THE MODEL'S OWN FORMAT, NOT OURS.
#
# The first version of this prompt said "reply with ONLY the proof term or
# tactic block ... no restatement of the theorem". Goedel-Prover-V2 declined
# it on the very first request, returning
#
#     theorem lean_workbook_plus_10000 : 1 + 1 = 2 := by norm_num <;> ...
#
# -- a complete declaration, under a name from its own training set. That is
# not disobedience, it is what the model was TRAINED to emit: a whole Lean
# file. Insisting otherwise would have measured its willingness to follow our
# formatting instead of its ability to prove theorems, and understated it.
#
# So we ask the way it was trained and take the proof apart ourselves in
# `_extract_proof`. Same lesson this repo keeps relearning: a rule that lives
# only in prose is a rule the model can decline. The guard is the parser.
ASK = """Think about and solve the following problem step by step in Lean 4.

# Formal statement:
```lean4
{statement} := by
```

Do not use `sorry`, `admit`, `native_decide`, or `exact?`/`apply?` -- a proof
containing any of them is rejected."""


# A declaration header, with the modifiers and attributes Lean allows before
# it. `example` is included because a model told not to restate the theorem
# sometimes complies by using an anonymous one instead.
_DECL = re.compile(
    r"(?:\A|\n)[ \t]*(?:@\[[^\]]*\][ \t]*)*"
    r"(?:(?:private|protected|noncomputable|nonrec|partial)[ \t]+)*"
    r"(theorem|lemma|example)\b")

# What may stand in front of the goal, once the prose is gone. A helper lemma
# is legitimate -- it compiles ahead of the statement, exactly like the
# agent's own kept lemmas.
_PREAMBLE_OK = re.compile(
    r"\A[ \t]*(?:@\[|/-|--|open\b|set_option\b|universe\b|variable\b"
    r"|section\b|namespace\b|end\b|local\b|attribute\b"
    r"|(?:private|protected|noncomputable|nonrec|partial)[ \t]"
    r"|theorem\b|lemma\b|def\b|abbrev\b|instance\b|structure\b"
    r"|inductive\b|class\b)")


def _signature_end(text: str, start: int) -> int:
    """Index just past the `:=` that ends a declaration's signature.

    Depth-tracked, because `:=` occurs inside a signature too -- in a
    structure instance `{ carrier := s }` or a binder default -- and taking
    the first one would cut the statement in half. Returns -1 if there is
    none, which happens when the model wrote `theorem foo : P := by` across a
    truncated generation.
    """
    depth = 0
    index = start
    while index < len(text) - 1:
        char = text[index]
        pair = text[index:index + 2]
        if pair == "--":                                  # line comment
            index = text.find("\n", index)
            if index == -1:
                return -1
            continue
        if pair == "/-":                                   # block comment
            closed = text.find("-/", index)
            if closed == -1:
                return -1
            index = closed + 2
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "\u27e8":                             # anonymous constructor
            depth += 1
        elif char == "\u27e9":
            depth -= 1
        elif pair == ":=" and depth <= 0:
            return index + 2
        index += 1
    return -1


# Tactic names common enough that a line beginning with one is Lean and not
# English. Used only to tell code from prose, never to judge a proof.
_TACTIC_LINE = re.compile(
    r"(?m)^\s*(?:intro|intros|exact|apply|refine|simp|simpa|rw|rwa|norm_num"
    r"|ring|ring_nf|linarith|nlinarith|positivity|field_simp|constructor"
    r"|rcases|obtain|cases|induction|use|omega|decide|aesop|trivial|rfl"
    r"|have|show|calc|unfold|push_cast|gcongr|bound|convert|specialize"
    r"|by_cases|contrapose|exfalso|left|right|ext|funext|subst"
    # `by` ANCHORED AT LINE START, which is the whole difference. `by
    # trivial` on its own line is a Lean proof body; "divide both sides by
    # 3" has `by` mid-sentence, and neither real generation has a line that
    # begins with it.
    r"|by)\b")


def _looks_like_lean(text: str) -> bool:
    """Did the model get as far as writing code, or is this all prose?

    NOT a judgement of the proof -- only of whether there is one. A reply
    with no Lean in it is a generation that ran out of tokens before
    reaching the proof, which is our budget's fault and fixable; scoring it
    as a failed proof would blame the prover for our cap.

    MEASURED against the two real generations on disk (2577 and 3281 chars
    of `### Detailed Proof and Analysis`). Of every candidate marker, only a
    BARE `by` false-positived on them -- because mathematical English is
    full of it: "divide both sides by 3". No fence, no declaration, no `:=`
    and no tactic-initial line appeared in either. So bare `by` is excluded
    and the other four decide.
    """
    if "```" in text:
        return True
    if re.search(r"\b(theorem|lemma|example)\b", text):
        return True
    if ":=" in text:
        return True
    return bool(_TACTIC_LINE.search(text))


def _extract_proof(text: str) -> tuple:
    """Split a model's answer into (preamble, proof body).

    THE STATEMENT IN THE ANSWER IS DISCARDED, ALWAYS. The model restates the
    theorem under its own name, and sometimes restates it WRONG -- a
    weakened hypothesis, a dropped quantifier. Compiling what it sent back
    would let it win by proving something easier, which is the one failure
    mode that would make this whole spike lie. Only the body after `:=`
    survives; the goal compiled is ours.

    A helper lemma written before the goal is kept and returned as preamble,
    because that is a real proof strategy and the agent is allowed it too.
    """
    matches = list(_DECL.finditer(text))
    if not matches:
        return "", text.strip()          # the model complied; body as sent

    # The LAST declaration is the goal -- anything before it is a helper.
    final = matches[-1]
    cut = _signature_end(text, final.end())
    if cut == -1:
        return "", text.strip()          # unparseable; let it fail honestly

    body = text[cut:].strip()
    preamble = text[:final.start()].strip()

    # Drop prose and stray tokens ahead of the first real declaration. The
    # calibration reply began with a bare "4" before its theorem; left in,
    # that is a syntax error blamed on the proof.
    lines = preamble.splitlines()
    while lines and not _PREAMBLE_OK.match(lines[0]):
        lines.pop(0)
    # `import` is legal only at the top of a file and build_source already
    # supplies the imports.
    kept = [line for line in lines if not line.lstrip().startswith("import ")]
    return "\n".join(kept).strip(), body


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


def _cache_read() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _cache_write(store: dict) -> None:
    try:
        CACHE.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    except OSError:
        pass          # a cache that cannot be written must not end the run


def _ask_local(url: str, model: str, statement: str, timeout: float,
               max_tokens: int = 1024) -> str:
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
        "max_tokens": max_tokens,
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
    parser.add_argument(
        "--max-tokens", type=int, default=4096,
        help="cap on the generation. Goedel-Prover-V2 is a REASONING model: "
             "it writes a long '### Detailed Proof and Analysis' prose "
             "section and only then the Lean block. MEASURED at 1024 -- two "
             "goals returned 2577 and 3281 characters of pure prose, "
             "truncated mid-sentence, with no Lean in them at all. Cutting "
             "this to save time measures the cap, not the prover.")
    parser.add_argument(
        "--regenerate", action="store_true",
        help="ignore the cache and ask the prover again. Without it, a goal "
             "already generated is reused, so re-running after fixing the "
             "COMPILE side costs seconds instead of minutes.")
    parser.add_argument(
        "--calibrate", action="store_true",
        help="ask the server for ONE short answer and report how fast it "
             "was. Run this BEFORE --run: it tells you whether a real "
             "generation can finish inside your --timeout.")
    # 1800s, not 300s. MEASURED: three goals against a CPU-served
    # Goedel-Prover-V2-8B at Q4 all failed with `SERVER TimeoutError` at 300s.
    #
    # AND THE REASON GIVEN HERE FIRST WAS WRONG. It blamed prompt
    # evaluation -- "an 8B model on CPU spends minutes on it before emitting
    # a token". llama-server's own log says otherwise:
    #
    #   prompt eval time = 5969.85 ms /  132 tokens (22.11 tokens per second)
    #          eval time = 212422.03 ms / 1024 tokens (4.82 tokens per second)
    #
    # Prompt evaluation is SIX SECONDS and runs 4.5x faster per token than
    # generation. All 212 of those seconds are generation, and the request
    # ended by hitting --max-tokens exactly (n_gen 1024, truncated = 0), not
    # by finishing. The timeout has to cover max_tokens / ~4.8 tok/s and
    # essentially nothing else.
    #
    # A timeout shorter than the model is slow measures the timeout, not the
    # prover -- which was the right conclusion from the wrong reason.
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--cheapest-first", action="store_true",
        help="order by what OUR agent spent, ascending. On slow hardware the "
             "first goal should be the one most likely to finish, not "
             "whichever sorts first alphabetically.")
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

    if args.run or args.calibrate:
        # PRE-FLIGHT, BEFORE ANY GENERATION.
        #
        # MEASURED, third failed attempt at this spike: three proofs were
        # generated at ~4 minutes each and then every one came back
        # `unavailable` -- LeanOutcome.UNAVAILABLE, "no Lean on this
        # machine". The shell had no MRA_LEAN_PROJECT, so there was nothing
        # to compile against. Eleven minutes of CPU spent to learn an
        # environment variable was missing, and "0 of 3" printed as though
        # the prover had been tested.
        #
        # Every failure of this experiment so far has had the same shape:
        # spend first, discover the problem afterwards. So both paths now
        # check the compiler is reachable BEFORE the first request, and say
        # exactly what to set.
        # `_local.lean_available()`, NOT `lean_toolchain_works()`.
        #
        # MEASURED, fourth failed attempt: the first version of this check
        # used `lean_toolchain_works`, which only asks whether `lean
        # --version` prints a banner. It printed "lean: reachable" and the
        # very next compile returned UNAVAILABLE -- because the compile goes
        # through `lake env lean` inside MRA_LEAN_PROJECT, and none of that
        # is what `lean --version` tests. A pre-flight that checks something
        # other than what the run needs is a pre-flight that lies.
        #
        # `_local.lean_available` was already here, already checks
        # MRA_LEAN_PROJECT and `lake`, and already returns the reason. Its
        # own docstring makes this exact argument: "a run that silently
        # scored 0% would be worse than one that refused to start."
        from math_v2 import _local
        from verifiers.lean_runner import LeanOutcome
        ready, why = _local.lean_available()
        if not ready and args.calibrate and not args.run:
            # A WARNING, NOT A REFUSAL, for calibration alone. It compiles
            # nothing and spends under a minute, so checking the server's
            # speed before Lean is set up is a legitimate thing to do.
            # Refusing it made `--calibrate` depend on something it does not
            # use.
            print(f"note: Lean cannot compile here ({why}), so --run would")
            print("      refuse. Calibrating anyway -- it compiles nothing.\n")
        elif not ready:
            print(f"Lean cannot compile here: {why}\n")
            print("Nothing this prover returns could be checked, so")
            print("generating first would throw the results away -- as it")
            print("has now done twice.\n")
            print("  $env:MRA_LEAN_PROJECT = "
                  '"C:\\Users\\SiyaJethliya\\Projects\\lean-workspace"')
            print("\nThat is the Lake project with Mathlib, toolchain")
            print("4.33.0. Set it in the SAME shell you run this from.")
            return 1
        if ready:
            print(f"lean: {_local.LEAN_PROJECT}")

        # AND ONE REAL COMPILE, because `lake --version` succeeding still
        # does not prove `import Mathlib` resolves. This is the only check
        # that tests what every goal in the run will do. Cached, and it is
        # the cold Mathlib import -- slow once, then free.
        if args.run:
            # ONLY FOR --run. Calibration compiles nothing, so making it pay
            # for a cold Mathlib import would be charging it for a
            # dependency it does not have.
            print("checking `import Mathlib` actually resolves ...",
                  flush=True)
            probe, _ = asyncio.run(
                _compile("theorem probe : True", "by trivial", []))
            if probe.outcome is not LeanOutcome.COMPILED:
                print(f"  it does not: {probe.outcome.value}")
                for line in (probe.output or "").splitlines()[:8]:
                    print(f"  {line}")
                print("\nEvery goal would fail for this reason and none of")
                print("it would be about the prover. Fix this first.")
                return 1
            print("  ok\n")

    if args.calibrate:
        # RUN THIS FIRST. A generation that cannot finish inside --timeout
        # measures the timeout, not the prover -- which is exactly what the
        # first attempt at this spike did: three `SERVER TimeoutError`s and
        # no information.
        #
        # TWO POINTS, NOT ONE, and the reason is a mistake this made.
        # The first version timed ONE 64-token answer and divided: 22s / 64
        # = 2.8 tok/s. llama-server's own log for a real request says
        #
        #   prompt eval time =   5969.85 ms /  132 tokens (22.11 tok/s)
        #          eval time = 212422.03 ms / 1024 tokens ( 4.82 tok/s)
        #
        # so generation is 4.82 tok/s and there is a ~6s FIXED prompt cost
        # in front of it. Amortising that fixed cost over 64 tokens
        # understated the rate by 42%, and every time estimate built on it
        # was 1.7x too pessimistic.
        #
        # Two generations of different lengths separate the two: the slope
        # between them is the marginal per-token cost with the fixed cost
        # cancelled, and the intercept IS the fixed cost. Both are then
        # reported, because a timeout needs the total and a corpus estimate
        # needs the slope.
        SHORT, LONG = 16, 128
        print(f"calibrating: two answers ({SHORT} and {LONG} tokens), timed ...")
        points = []
        for cap in (SHORT, LONG):
            started = time.monotonic()
            try:
                reply = _ask_local(args.url, args.model, "1 + 1 = 2",
                                   args.timeout, max_tokens=cap)
            except Exception as exc:  # noqa: BLE001
                print(f"  the server did not answer: {type(exc).__name__}: {exc}")
                print("  Is llama-server still running on that --url?")
                return 1
            points.append((cap, max(time.monotonic() - started, 1e-6)))
            print(f"  {cap:>4} tokens -> {points[-1][1]:>6.1f}s")

        (n1, t1), (n2, t2) = points
        # Guard the degenerate case: if the longer request was not slower,
        # the server is caching or the model stopped early, and the slope is
        # meaningless. Fall back to the honest end-to-end figure.
        if t2 > t1 and n2 > n1:
            per_token = (t2 - t1) / (n2 - n1)
            fixed = max(t1 - n1 * per_token, 0.0)
            rate = 1.0 / per_token if per_token > 0 else 0.0
            print(f"  -> {rate:.1f} tok/s generation, {fixed:.0f}s fixed "
                  f"overhead per request")
        else:
            rate = n2 / t2
            fixed = 0.0
            print(f"  -> {rate:.1f} tok/s end-to-end (the two points did not "
                  f"separate; treating it as one)")
        print(f"  reply began: {reply[:70]!r}")
        print()

        needed = fixed + args.max_tokens / max(rate, 1e-6)
        print(f"  a {args.max_tokens}-token generation would take "
              f"~{needed:.0f}s ({needed / 60:.0f} min).")
        if needed > args.timeout:
            print(f"  YOUR --timeout IS {args.timeout:.0f}s. TOO SHORT. Raise "
                  f"it to at least {needed * 1.5:.0f}, or lower --max-tokens.")
        else:
            print(f"  --timeout {args.timeout:.0f}s has room. Safe to --run.")
        print()
        print(f"  {len(corpus)} goals at ~{needed / 60:.0f} min each is "
              f"~{len(corpus) * needed / 3600:.1f}h for the full corpus.")
        print("  Use --limit and --cheapest-first to spike a few first.")
        print()
        print("  NOTE: Goedel-Prover-V2 reasons in prose before writing any")
        print("  Lean. MEASURED: at --max-tokens 1024 two goals returned")
        print("  2577 and 3281 characters of analysis and no proof at all.")
        print("  A cap that stops it mid-thought measures the cap.")
        return 0

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

    goals = sorted(corpus, key=lambda g: g["agent_input_tokens"]) \
        if args.cheapest_first else corpus
    goals = goals[: args.limit] if args.limit else goals
    closed = []
    unreachable = []
    truncated = []
    store = _cache_read()
    if store:
        print(f"cache: {len(store)} generation(s) on disk, reused for free\n")
    for index, goal in enumerate(goals, 1):
        key = f"{goal['goal_id']}::{args.model}"
        cached = store.get(key)
        if cached and not args.regenerate:
            proof, elapsed = cached["proof"], cached.get("seconds", 0.0)
            reused = True
        else:
            reused = False
            try:
                started = time.monotonic()
                proof = _ask_local(args.url, args.model, goal["statement"],
                                   args.timeout, args.max_tokens)
                elapsed = time.monotonic() - started
            except Exception as exc:  # noqa: BLE001 - one failure must not stop the sweep
                unreachable.append((goal["goal_id"], type(exc).__name__))
                print(f"[{index}/{len(goals)}] {goal['goal_id']:22} SERVER "
                      f"{type(exc).__name__} -- not a result")
                continue
            # WRITTEN BEFORE THE COMPILE, not after. The compile is what
            # failed last time, and a proof only saved on success is a proof
            # lost exactly when the cache would have helped.
            store[key] = {"proof": proof, "seconds": elapsed,
                          "model": args.model}
            _cache_write(store)
        # NO LEAN AT ALL IS NOT A FAILED PROOF.
        #
        # MEASURED on the two real generations in the cache: 2577 and 3281
        # characters of `### Detailed Proof and Analysis` prose, cut off
        # mid-sentence by --max-tokens, containing no theorem, no tactic and
        # no code fence. Compiling that would produce a syntax error and be
        # counted as the prover failing to prove the goal. It is the
        # generation budget running out, which is our fault and fixable.
        if not _looks_like_lean(proof):
            truncated.append((goal["goal_id"], len(proof)))
            print(f"[{index}/{len(goals)}] {goal['goal_id']:22} "
                  f"NO LEAN IN REPLY -- not a result "
                  f"({len(proof)} chars of prose, {elapsed:.0f}s)")
            continue

        # The model restated the theorem; keep OUR statement and its body.
        helper, body = _extract_proof(proof)
        lemmas = list(goal["lemmas"]) + ([helper] if helper else [])
        result, verdict = asyncio.run(
            _compile(goal["statement"], body, lemmas))
        from domain.verdict import VerificationStatus
        from verifiers.lean_runner import LeanOutcome
        ok = verdict.status is VerificationStatus.TRUE
        if result.outcome is LeanOutcome.UNAVAILABLE:
            # NOT A MISS, for the same reason a server timeout is not one:
            # the proof was never judged. The pre-flight should have caught
            # this, so reaching here means Lean died mid-run.
            # WITH THE REASON. `_util.lean_runner` catches every exception
            # into `LeanResult(UNAVAILABLE, f"Lean could not be run: {exc}")`,
            # so the cause IS carried -- and the first version of this line
            # printed none of it, which is why a whole run said only
            # "LEAN UNAVAILABLE" and named nothing to fix.
            detail = (result.output or "").strip() or "no detail reported"
            unreachable.append((goal["goal_id"], detail[:200]))
            print(f"[{index}/{len(goals)}] {goal['goal_id']:22} "
                  f"LEAN UNAVAILABLE -- not a result")
            print(f"    {detail[:300]}")
            continue
        closed.append((goal["goal_id"], ok, proof))
        print(f"[{index}/{len(goals)}] {goal['goal_id']:22} "
              f"{'PROVED' if ok else result.outcome.value}"
              f"   ({elapsed:.0f}s to generate{', cached' if reused else ''})")

    won = [g for g, ok, _ in closed if ok]
    print()
    print("=" * 66)
    if unreachable:
        # SAID SEPARATELY, because "0 of 0" reads like a result and is not
        # one. A server that never answered has measured the server.
        print(f"  {len(unreachable)} goal(s) NEVER REACHED THE PROVER:")
        for goal_id, why in unreachable:
            print(f"    {goal_id:22} {why}")
        print("  Those are not misses. Raise --timeout, or serve a smaller")
        print("  quantisation, and run them again.")
        print()
    if truncated:
        print(f"  {len(truncated)} generation(s) CONTAINED NO LEAN:")
        for goal_id, size in truncated:
            print(f"    {goal_id:22} {size:>6} chars of prose, no theorem")
        print("  The reasoning ran past --max-tokens before reaching the")
        print("  proof. Not misses -- raise --max-tokens and note that at")
        print("  the measured 4.8 tok/s each extra 1000 tokens is ~3.5 more")
        print("  minutes per goal.")
        print()
    if not closed:
        print("  NOTHING WAS SCORED. No conclusion is available.")
        return 1
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

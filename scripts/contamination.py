"""Has the model memorised ProofNet's statements? A recall probe.

WHY THIS GATES EVERY EXTERNAL NUMBER

ProofNet's formal statements are public and have been for years. So a proof
rate on ProofNet has two readings -- the agent reasoned it out, or the model
had seen the problem -- and for a technical audience that is the FIRST
question, not a closing caveat. Until it is addressed a ProofNet rate is not a
capability claim.

WHAT THIS MEASURES, AND WHAT AN EARLIER VERSION GOT WRONG

The first version of this script compared the agent's accepted PROOF against
ProofNet's reference text by character 5-gram overlap. That measured nothing:
every ProofNet entry ships its statement with the proof replaced by `sorry`
(verified -- 0 of 182 notes contain `:= by`), so there are no reference proofs
to compare against, and a proof scored against a STATEMENT is just measuring
how much of the signature the proof happens to restate.

The answerable question is about the STATEMENT, and it is answerable because
formalising is the step ProofNet publishes. Given only a theorem's NAME and
its textbook chapter, a model that has never seen ProofNet cannot reproduce
its exact formalisation -- the choice of `Set.Finite` over `Finite`, of
`Filter.Tendsto` over an epsilon-delta spelling, of argument order and
implicit binders, is one formalisation among many. A model that reproduces it
closely has seen it.

So: ask for the statement, given the name. Compare to the reference by
character 5-gram Jaccard -- characters because Lean is dense in punctuation a
word tokeniser destroys, Jaccard because it is order-insensitive.

HOW TO READ THE SCORE. There is no threshold that proves memorisation, and
this script does not pretend to one. What it produces is a distribution, and
the useful comparison is between the goals that PROVED and the goals that did
not. If the proved ones are recalled more accurately, recall is a live
explanation for the rate. If both groups score alike, recall is not what
separates them.

COST. One short completion per goal, no tools and no Lean -- cheap next to a
proving run, but not free. `--dry-run` is the DEFAULT and spends nothing: it
prints the prompts and the reference each would be scored against.

    python scripts/contamination.py --goals eval/proofnet-182.json
    python scripts/contamination.py --goals eval/proofnet-182.json --run --limit 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

N = 5

# Three failures with nothing yet scored means the setup is wrong, not that
# three goals are awkward. MEASURED elsewhere in this repo: an invalid API
# key once let 53 goals run and fail identically because the abort only
# counted RAISED exceptions -- the same shape of waste this prevents.
GIVE_UP_AFTER = 3

from llm.exhaustion import advice, is_hopeless  # noqa: E402

ASK = """State the Lean 4 theorem named `{name}` from {area}.

Reply with the theorem signature only -- no proof, no explanation, no code
fence. If you do not know this specific theorem, reply exactly: UNKNOWN"""


def shingles(text: str) -> set:
    squeezed = " ".join((text or "").split())
    return {squeezed[i:i + N] for i in range(max(0, len(squeezed) - N + 1))}


def jaccard(left: str, right: str) -> float:
    a, b = shingles(left), shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def reference(note: str) -> str:
    """The statement, with the `sorry` and the `open` preamble stripped.

    The preamble is boilerplate shared by every goal in a chapter, so leaving
    it in would give every answer a floor of similarity for free.
    """
    body = (note or "").split(":= sorry")[0]
    lines = [ln for ln in body.splitlines()
             if not ln.strip().startswith("open ")]
    return "\n".join(lines).strip()


def expand(paths) -> list:
    """Resolve each argument, expanding a glob the shell did not.

    MEASURED: `--results eval\\results\\*.json` works in bash and does
    NOT in PowerShell, which passes the pattern through literally. The
    script then read a file named `*.json`, found nothing, and reported "no
    goals in this file have a recorded outcome" -- a message about the goals
    for a problem in the arguments. Expanding here makes the same command
    work in both shells.
    """
    import glob as _glob

    resolved, missing = [], []
    for path in paths:
        hits = _glob.glob(str(path))
        if hits:
            resolved.extend(sorted(hits))
        else:
            missing.append(str(path))
    for path in missing:
        # SAID OUT LOUD. `outcomes` catches OSError and returns nothing, so
        # a path that matches no file is indistinguishable from a file with
        # no rows in it -- which is exactly how the PowerShell failure came
        # to look like a fact about the data.
        print(f"warning: --results {path} matched no file")
    return resolved


def outcomes(paths) -> dict:
    """`{goal_id: outcome}` from results files, so scores can be split by it."""
    found = {}
    for path in expand(paths):
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))["results"]
        except (ValueError, OSError, KeyError):
            continue
        for row in rows:
            if row.get("tier") == "proofnet":
                found[row["goal_id"]] = row.get("outcome")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--goals", default="eval/proofnet-182.json")
    parser.add_argument("--results", nargs="*", default=[],
                        help="results files, to split scores by outcome")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--decided-only", action="store_true",
        help="probe only goals that already have a recorded outcome. The "
             "proved-versus-not comparison is the answerable question, and "
             "an undecided goal contributes nothing to it -- so this is "
             "both cheaper and more targeted. Needs --results.")
    parser.add_argument("--run", action="store_true",
                        help="actually call the model. Off by default: this "
                             "spends money, and a dry run costs nothing.")
    args = parser.parse_args(argv)

    goals = json.loads(Path(args.goals).read_text(encoding="utf-8"))
    goals = [g for g in goals if (g.get("note") or "").strip()]
    if args.decided_only:
        if not args.results:
            print("--decided-only needs --results to know which are decided.")
            return 2
        known = outcomes(args.results)
        if not known:
            print("None of the --results files contained a ProofNet row, so")
            print("there is no outcome to select on. In PowerShell a glob is")
            print("NOT expanded by the shell -- this script expands it now,")
            print("so check the path itself:")
            print("  --results eval\\results\\*.json")
            return 2
        goals = [g for g in goals if g["id"] in known]
        if not goals:
            print(f"{len(known)} goals have a recorded outcome, but none of "
                  f"them are in {args.goals}.")
            print("The results and the goal file are for different sets.")
            return 2
    if args.limit:
        goals = goals[: args.limit]
    decided = outcomes(args.results)

    if not args.run:
        print(f"DRY RUN. {len(goals)} goals would be probed with one short "
              f"completion each.\nNothing is spent. Add --run to probe.\n")
        sample = goals[0]
        print("the prompt, for one goal:")
        print("-" * 62)
        print(ASK.format(name=sample["id"], area=sample.get("area", "ProofNet")))
        print("-" * 62)
        print("\nscored against this reference (preamble and `sorry` removed):")
        print("-" * 62)
        print(reference(sample.get("note"))[:600])
        print("-" * 62)
        print(f"\n{len(decided)} of them have a recorded outcome, so scores "
              f"can be split into proved and not.")
        return 0

    from llm import get_model
    model = get_model()

    scored = []
    answers = {}
    failures = 0
    for index, goal in enumerate(goals, 1):
        ref = reference(goal.get("note"))
        try:
            reply = model.invoke(ASK.format(
                name=goal["id"], area=goal.get("area", "ProofNet")))
            said = getattr(reply, "text", None) or getattr(reply, "content", "")
            if callable(said):
                said = said()
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the sweep
            # WITH THE MESSAGE. Printing only `type(exc).__name__` gave
            # `FAILED ChatGoogleGenerativeAIError` and nothing to act on --
            # the provider puts the reason (a bad model name, an exhausted
            # quota, a rejected key) in the message, and this threw it away.
            # The same mistake as reporting "no goals have an outcome" for
            # an unexpanded glob: a diagnostic that cannot say WHY is not a
            # diagnostic.
            print(f"[{index}/{len(goals)}] {goal['id']}: FAILED "
                  f"{type(exc).__name__}: {str(exc)[:400]}")
            if is_hopeless(exc):
                # STOP AT ONCE, not after GIVE_UP_AFTER. A spend cap or an
                # exhausted quota will refuse every remaining goal, so the
                # only thing more attempts buy is more of the same line.
                print()
                print(advice(exc))
                return 1
            failures += 1
            if failures >= GIVE_UP_AFTER and not scored:
                # NOTHING has succeeded yet, so this is not a flaky goal --
                # it is the configuration. Grinding through the remaining
                # goals would produce the same line 45 more times.
                print(f"\nStopping after {failures} consecutive failures with "
                      f"nothing scored.")
                print("This is the model or the key, not the goals. The "
                      "message above says which.")
                return 1
            continue
        failures = 0
        said = str(said).strip()
        declined = said.upper().startswith("UNKNOWN")
        score = 0.0 if declined else jaccard(said, ref)
        scored.append((goal["id"], score, declined, decided.get(goal["id"])))
        # KEPT so the null baseline below costs nothing extra.
        answers[goal["id"]] = (said, ref, goal.get("area", ""))
        print(f"[{index}/{len(goals)}] {goal['id']:26} "
              f"{'declined' if declined else f'{score:.3f}'}")

    if not scored:
        print("nothing scored")
        return 1

    print()
    print("RECALL SCORES")
    print("-" * 62)
    live = [s for _, s, d, _ in scored if not d]
    print(f"  probed {len(scored)}, declined {sum(1 for *_, d, _ in scored if d)}")
    if live:
        print(f"  median {statistics.median(live):.3f}   "
              f"max {max(live):.3f}")

    # THE NULL BASELINE, AND WHY IT DECIDES WHETHER ANY OF THIS MEANS
    # ANYTHING.
    #
    # A Jaccard of 0.35 against the reference is uninterpretable on its own.
    # Lean statements about the same material share a great deal of surface
    # -- `theorem exercise_`, the binders, `Polynomial`, the arrows -- so a
    # model that had never seen ProofNet and simply wrote a competent
    # formalisation of a canonical theorem would still score well above
    # zero. Without knowing what that floor IS, a high score proves nothing.
    #
    # So each answer is also scored against every OTHER goal's reference.
    # Those pairings are wrong by construction, so their distribution is the
    # score achievable from shared notation alone. Costs no extra calls: it
    # is the same answers, re-compared.
    #
    # The same-chapter figure is the stricter control -- goals within one
    # textbook chapter share vocabulary and typeclass setup, so it removes
    # more of the free similarity than the overall figure does.
    if len(answers) > 2:
        off, off_same_area = [], []
        for goal_id, (said, _ref, area) in answers.items():
            if said.upper().startswith("UNKNOWN"):
                continue
            for other_id, (_s, other_ref, other_area) in answers.items():
                if other_id == goal_id:
                    continue
                value = jaccard(said, other_ref)
                off.append(value)
                if area and area == other_area:
                    off_same_area.append(value)
        if off and live:
            print()
            print("NULL BASELINE -- what shared notation alone is worth")
            print("-" * 62)
            on = statistics.median(live)
            print(f"  matched   (answer vs ITS reference)      median {on:.3f}")
            print(f"  mismatched(answer vs ANOTHER reference)  median "
                  f"{statistics.median(off):.3f}   n={len(off)}")
            if off_same_area:
                print(f"  mismatched, same chapter                 median "
                      f"{statistics.median(off_same_area):.3f}   "
                      f"n={len(off_same_area)}")
            control = statistics.median(off_same_area or off)
            lift = on - control
            print(f"  lift over the control  {lift:+.3f}")
            if lift < 0.05:
                print("  The matched score is no better than a deliberately")
                print("  WRONG pairing. There is no recall signal here at all;")
                print("  the raw score is shared notation.")
            elif lift < 0.15:
                print("  A small lift. Some of the raw score is specific to the")
                print("  right statement, most of it is notation.")
            else:
                print("  The matched score is well above a wrong pairing, so the")
                print("  model is reproducing THESE formalisations specifically.")

    groups = {}
    for goal_id, score, declined, outcome in scored:
        if outcome:
            groups.setdefault("proved" if outcome == "proved" else "not proved",
                              []).append(score)
    if len(groups) == 2:
        print()
        print("SPLIT BY OUTCOME -- the comparison that is actually answerable")
        print("-" * 62)
        for label, scores in sorted(groups.items()):
            print(f"  {label:12} n={len(scores):<3} median {statistics.median(scores):.3f}")
        pm = statistics.median(groups["proved"])
        um = statistics.median(groups["not proved"])
        print(f"  difference {pm - um:+.3f}")
        if abs(pm - um) < 0.05:
            print("  Both groups are recalled about equally well, so recall is")
            print("  NOT what separates what proved from what did not.")
        elif pm > um:
            print("  Proved goals are recalled MORE accurately. Recall is a")
            print("  live explanation for the rate and cannot be dismissed.")
        else:
            print("  Proved goals are recalled LESS accurately, the opposite")
            print("  of what memorisation would predict.")

    print()
    print("No score proves memorisation, and nothing outside the provider can")
    print("settle it. This measures whether the model can reproduce a public")
    print("formalisation from its name alone, and whether that tracks which")
    print("goals proved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

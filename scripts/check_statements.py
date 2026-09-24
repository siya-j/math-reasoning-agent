"""Audit the goals that LEFT the denominator, by round-tripping them.

    # costs nothing, prints the prompts it would send
    python3 scripts/check_statements.py --results eval/results/test-186.json \
                                        --goals eval/proofnet-test-186.json

    # actually run it
    python3 scripts/check_statements.py --results eval/results/test-186.json \
                                        --goals eval/proofnet-test-186.json \
                                        --run --out eval/results/statement-audit.json

Every external number this project reports is quoted over `valid_targets`,
and on the 186-goal ProofNet test run that denominator drops 32 goals: 19
REFUTED and 13 SUSPECT_STATEMENT. The refutations are bought with a
compilation. The thirteen are not — the handoff calls them "the one exclusion
that is not bought with a compilation" — and they move the headline rate by
several points on the agent's own unverified reading.

This takes each such statement, has a model describe it in English WITHOUT
showing it the textbook wording, then asks whether that description and the
textbook agree. The method is HERALD-AF's, from REAL-Prover
(arXiv:2505.20613), pointed at auditing a denominator rather than filtering
training data.

IT DECIDES NOTHING. The output is a list of goals for a person to open, in
priority order. It does not and must not change `valid_targets`: a model
judging whether two sentences agree is a model deciding what is true, and
that is the one job this system never gives one.

`--dry-run` is the default and spends nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import elaborate as elaboration_module  # noqa: E402
from eval import roundtrip  # noqa: E402

# Three failures with nothing scored means the setup is wrong, not that three
# goals are awkward. The same guard the contamination probe uses, for the
# same reason: an invalid key once let 53 goals run and fail identically.
GIVE_UP_AFTER = 3


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows_of(data):
    return data["results"] if isinstance(data, dict) and "results" in data else data


def formal_of(result: dict, goal: dict) -> str:
    """The theorem as Lean saw it.

    Prefers the statement the RUN recorded over the one in the goals file,
    because that is what was actually compiled, and the two can differ if the
    goals file has been edited since.
    """
    return (result.get("statement") or goal.get("note") or goal.get("goal") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--goals", required=True)
    parser.add_argument("--run", action="store_true",
                        help="actually call the model (default is a dry run)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--outcomes", nargs="+", default=list(roundtrip.AUDITABLE),
                        help="which outcomes to audit")
    parser.add_argument("--out", default="", help="write the assessments here")
    parser.add_argument(
        "--no-elaborate", action="store_true",
        help=("skip Lean and back-translate the SOURCE. Faster, and "
              "unreliable: source and elaborated form differ exactly where "
              "the bugs are."),
    )
    args = parser.parse_args()

    goals = {str(g["id"]): g for g in load(args.goals)}
    results = rows_of(load(args.results))

    targets = [
        r for r in results
        if str(r.get("outcome")) in args.outcomes and str(r.get("goal_id")) in goals
    ]
    if args.limit:
        targets = targets[: args.limit]

    print(f"{len(results)} rows, {len(targets)} to audit "
          f"({', '.join(args.outcomes)})\n")

    if not args.run:
        print("DRY RUN — nothing sent, nothing spent. "
              "Add --run to actually check.\n")
        for result in targets[:2]:
            goal = goals[str(result["goal_id"])]
            print("=" * 68)
            print(f"  {result['goal_id']}   [{result['outcome']}]")
            print("=" * 68)
            print(roundtrip.BACK_TRANSLATE.format(
                formal=formal_of(result, goal)[:600]))
            print("\n  --- then, with the reply in place of the second field ---\n")
            print(roundtrip.JUDGE.format(
                informal=(goal.get("informal") or "")[:400],
                back_translation="<the reply to the prompt above>"))
            print()
        if len(targets) > 2:
            print(f"  ... and {len(targets) - 2} more like this.")
        return 0

    import llm  # imported late so a dry run needs no model

    model = llm.get_model()

    if args.no_elaborate:
        print("NOT ELABORATING. The model will read the source text, which\n"
              "reads the way a mathematician would assume rather than the way\n"
              "Lean understood it. Four broken statements passed this way.\n")
    assessments = []
    consecutive_failures = 0

    for index, result in enumerate(targets, 1):
        goal = goals[str(result["goal_id"])]
        statement = formal_of(result, goal)
        elaborated = None
        if not args.no_elaborate:
            elaborated = elaboration_module.elaborate(
                preamble=result.get("preamble") or goal.get("note", ""),
                statement=statement,
            )
        assessment = roundtrip.assess(
            goal_id=str(result["goal_id"]),
            outcome=str(result["outcome"]),
            formal=statement,
            informal=goal.get("informal", ""),
            model=model,
            elaboration=elaborated,
        )
        assessments.append(assessment)

        if assessment.verdict == roundtrip.UNCLEAR and "failed" in assessment.why:
            consecutive_failures += 1
            if consecutive_failures >= GIVE_UP_AFTER:
                print(f"\n{GIVE_UP_AFTER} failures in a row with nothing scored. "
                      f"Stopping: {assessment.why}")
                break
        else:
            consecutive_failures = 0

        flag = "  <-- look at this one" if assessment.needs_a_human else ""
        if assessment.verdict == roundtrip.BROKEN:
            flag = "  (compiler agrees it is unusable)"
        print(f"  [{index:3d}/{len(targets)}] {assessment.goal_id:38s} "
              f"{assessment.outcome:18s} {assessment.verdict:9s}{flag}")

    summary = roundtrip.summarize(assessments)
    print("\n" + "=" * 68)
    print(f"  assessed               {summary['assessed']}")
    for key, count in summary["by_outcome_and_verdict"].items():
        print(f"    {key:34s} {count}")
    print(f"  worth opening          {summary['needs_a_human']}")
    print("=" * 68)

    broken = sum(1 for a in assessments if a.verdict == roundtrip.BROKEN)
    if broken:
        print(f"\n  {broken} statement(s) do not elaborate cleanly. Those "
              "exclusions are\n  CONFIRMED by the compiler, not questioned "
              "by it, and no model was\n  asked about them.")

    if summary["needs_a_human"]:
        print("\n  A SUSPECT goal that round-trips as a MATCH means the agent")
        print("  called a statement broken that an independent reading says is")
        print("  faithful. Either the agent was wrong -- and valid_targets is")
        print("  too small, making the headline rate too HIGH -- or the")
        print("  textbook claim is itself false.")
        print("\n  A REFUTED goal that matches means Lean compiled the negation")
        print("  of something that faithfully renders the book.")
        print("\n  This tool does not decide which. Open them.")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "summary": summary,
            "note": ("Advisory only. These assessments must not be used to "
                     "change valid_targets; a model judging agreement is a "
                     "model deciding truth."),
            "assessments": [vars(a) for a in assessments],
        }, indent=2), encoding="utf-8")
        print(f"\nSaved to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

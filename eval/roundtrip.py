"""Does the formal statement say what the textbook said? A round-trip check.

ADAPTED FROM HERALD-AF (Shen et al., REAL-Prover, arXiv:2505.20613). Their
auto-formalisation pipeline keeps a generated Lean statement only if it
survives a round trip: formalise, translate BACK to natural language with a
second model, then judge whether the back-translation still says what the
original said. Mismatches are discarded rather than trained on.

WHY IT MATTERS HERE, and it is not auto-formalisation.

Every external number this project reports rests on `valid_targets`, and
that denominator excludes 32 of ProofNet's 186 test goals. Nineteen are
REFUTED, which is bought with a compilation and is therefore a fact. Thirteen
are SUSPECT_STATEMENT, and the handoff says plainly what those are: "the
agent's own reading, and nothing checks it ... the one exclusion that is not
bought with a compilation". Thirteen goals decide roughly eight points of the
headline rate on the agent's unverified say-so.

This gives those goals independent evidence. It also explains the refutations:
if a refuted statement round-trips FAITHFULLY, then the textbook claim itself
is false or ProofNet mis-transcribed it, and that is worth knowing.

TWO CONSTRAINTS, both load-bearing.

1. THE BACK-TRANSLATOR NEVER SEES THE ORIGINAL. Show it the informal text
   and it will paraphrase that instead of reading the Lean, and the check
   becomes a test of whether the model can copy. The two channels are kept
   apart for exactly the reason the guard keeps prose apart from recorded
   tool results.

2. THIS IS TRIAGE, NOT VERIFICATION. Its output must never silently move a
   goal in or out of `valid_targets`. A language model judging whether two
   sentences agree is a language model deciding what is true, which is the
   one thing this architecture exists to forbid. It ranks goals for a human
   to look at, and says so in its own output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from llm.reply import text_of

# The exclusions worth auditing. PROVED and NOT_PROVED goals were tested as
# written, so their statements are not in question; these two are the ones
# that left the denominator.
AUDITABLE = ("suspect_statement", "refuted")

MATCH = "match"
MISMATCH = "mismatch"
UNCLEAR = "unclear"
# Not a reading at all: Lean could not make sense of the statement. This one
# is BOUGHT WITH A COMPILATION and outranks anything a model says about the
# text, so it is decided before the model is asked.
BROKEN = "broken"


BACK_TRANSLATE = """Read this Lean 4 theorem statement and say, in plain
English, exactly what it asserts.

This is the FULLY ELABORATED form: every implicit argument and every numeric
literal's type has been made explicit by the compiler. Read the types that
are actually written, not the ones the notation suggests. `Icc (0 : Nat)
(1 : Nat)` is a two-element set of naturals, not the unit interval.

Describe only what is written. Do not prove it, do not comment on whether it
is true, and do not repair it if it looks wrong — if the statement says
something false or strange, your description must say that same false or
strange thing. A description that quietly corrects the statement is useless
here.

Answer in one or two sentences and nothing else.

{formal}"""


JUDGE = """Two descriptions of a mathematical claim are below. One is from a
textbook. The other was written by someone reading a formal translation of
it, who had never seen the textbook wording.

Decide whether they assert THE SAME CLAIM. Differences of wording, notation
or generality of phrasing do not matter. What matters is whether one could be
true while the other is false — a changed quantifier, a dropped hypothesis, a
different object, a reversed implication.

TEXTBOOK:
{informal}

FROM THE FORMAL STATEMENT:
{back_translation}

Answer in exactly this format, two lines, nothing else:

VERDICT: match
WHY: one sentence

VERDICT must be `match` if they assert the same claim, `mismatch` if the
formal translation lost or changed something that matters, or `unclear` if
the textbook description is too vague to compare."""


@dataclass(frozen=True)
class Assessment:
    """What the round trip found about one goal. Advisory, never decisive."""

    goal_id: str
    outcome: str
    verdict: str
    why: str
    back_translation: str = ""

    @property
    def needs_a_human(self) -> bool:
        """Which rows a person should actually open.

        A SUSPECT goal that round-trips as a MATCH is the interesting case:
        the agent called the statement broken, and an independent reading
        says it faithfully expresses the textbook. Either the agent was
        wrong, in which case the denominator is too small and the headline
        rate is too high, or the textbook claim is itself false.

        A REFUTED goal that round-trips as a MATCH is the other: Lean
        compiled a proof of the negation of something that faithfully
        renders the book.

        BROKEN is not in this list. It CONFIRMS the exclusion rather than
        questioning it -- the compiler agrees the statement is unusable, so
        there is nothing for a person to adjudicate.
        """
        return self.verdict == MATCH


def _first(pattern: str, text: str) -> str:
    found = re.search(pattern, text or "", re.MULTILINE | re.IGNORECASE)
    return found.group(1).strip() if found else ""


def parse_judgement(text: str) -> tuple[str, str]:
    """Read the judge's two lines. Anything unreadable becomes UNCLEAR.

    Defaulting to UNCLEAR rather than to MATCH or MISMATCH matters: an
    unparseable reply is an absence of evidence, and the two decisive values
    are the ones that would send a human looking or stop them.
    """
    verdict = _first(r"^\s*VERDICT\s*:\s*(\w+)", text).lower()
    why = _first(r"^\s*WHY\s*:\s*(.+)$", text)
    if verdict not in (MATCH, MISMATCH, UNCLEAR):
        return UNCLEAR, why or "the judge's reply could not be read"
    return verdict, why


def _content(reply) -> str:
    # Gemini returns content as a LIST of blocks, not a string. Reading it
    # naively is how `'list' object has no attribute 'strip'` reached a
    # live run after 25 unit tests passed on string-returning fakes.
    return text_of(reply)


def assess(goal_id: str, outcome: str, formal: str, informal: str,
           model, elaboration=None) -> Assessment:
    """Round-trip one statement. Never raises; returns UNCLEAR instead.

    `elaboration` is an eval.elaborate.Elaboration. When one is supplied the
    model reads the ELABORATED type rather than the source, which is the
    whole point: on the first live run this check called four broken
    ProofNet statements faithful because a model reading `Icc 0 1` describes
    the unit interval, while Lean had elaborated it over the naturals.

    A statement that does not elaborate is settled without asking the model
    at all. That verdict is bought with a compilation; the model's is not.
    """
    if elaboration is not None and not elaboration.usable:
        return Assessment(goal_id, outcome, BROKEN, elaboration.problem,
                          elaboration.text)
    if elaboration is not None:
        formal = elaboration.text

    if not (formal or "").strip():
        return Assessment(goal_id, outcome, UNCLEAR, "no formal statement")
    if not (informal or "").strip():
        return Assessment(
            goal_id, outcome, UNCLEAR,
            "no textbook statement to compare against",
        )

    try:
        # Channel one: the Lean, and nothing else.
        back = _content(model.invoke(BACK_TRANSLATE.format(formal=formal)))
    except Exception as exc:
        return Assessment(goal_id, outcome, UNCLEAR, f"back-translation failed: {exc}")

    if not back.strip():
        return Assessment(goal_id, outcome, UNCLEAR, "empty back-translation")

    try:
        # Channel two: sees both, but never saw the Lean.
        judged = _content(model.invoke(
            JUDGE.format(informal=informal.strip(), back_translation=back.strip())
        ))
    except Exception as exc:
        return Assessment(goal_id, outcome, UNCLEAR, f"judging failed: {exc}",
                          back.strip())

    verdict, why = parse_judgement(judged)
    return Assessment(goal_id, outcome, verdict, why, back.strip())


def summarize(assessments: list[Assessment]) -> dict:
    counts: dict[str, int] = {}
    for assessment in assessments:
        key = f"{assessment.outcome}/{assessment.verdict}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "assessed": len(assessments),
        "by_outcome_and_verdict": dict(sorted(counts.items())),
        "needs_a_human": sum(a.needs_a_human for a in assessments),
    }

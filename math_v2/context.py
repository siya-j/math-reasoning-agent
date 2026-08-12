"""Context for the math agent.

Per AGENT_BLUEPRINT.md §4: the field MUST be named `workdir`. That is what
makes this dataclass structurally compatible with `AuraContext`, which the
SessionFactory passes as `context_schema` so one context object feeds the whole
agent tree. Renaming it breaks unification for every agent, not just this one
(gotcha 6).

No other fields. The blueprint says to add them only if your own middleware
reads them; `iris_v2` is the single agent that does, because it is a
sub-supervisor. This one is a leaf spoke.

`from __future__ import annotations` is permitted here — context.py, agent.py
and prompt.py may use it; tool modules and their helpers may not (§5.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MathContext:
    workdir: str

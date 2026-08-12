"""math_v2 — a Deep Agent for symbolic and formal mathematics.

Re-exports the factory per AGENT_BLUEPRINT.md §2.

The re-export is LAZY. `agent.py` imports `deepagents` and `lean_common`, which
exist only inside the Aura tree; importing them eagerly would make the whole
package unimportable anywhere else, and the tool bodies, the guard and their
tests all need to run outside it. PEP 562 gives us both: `import math_v2` works
everywhere, `from math_v2 import create_math_v2_agent` works where it can.
"""

from __future__ import annotations

__all__ = ["create_math_v2_agent", "FACTORY_DESCRIPTION"]


def __getattr__(name: str):
    if name in __all__:
        from math_v2 import agent

        return getattr(agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

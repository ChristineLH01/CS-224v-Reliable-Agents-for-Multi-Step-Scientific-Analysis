"""Adapted SHELDON: baseline SHELDON plus the changes switched on by name.

Each change to SHELDON has a name in ADAPTATIONS and can be switched on alone (to measure
that change) or together with the others (`all`, the adapted SHELDON as a whole). With
none on, the driver runs SHELDON's own method, so the baseline never goes through this
package.

  method.py       the adapted method: SHELDON's run loop, copied verbatim, with one marked
                  hook per change
  early_stop.py   `early_stop`: end the run at a round boundary once the case is closed

This module imports nothing from SHELDON, so the registry can be read without it.
bmbench keeps its own copy of the names (bmbench.config.SHELDON_ADAPTATIONS); a test
keeps the two equal.
"""

from __future__ import annotations

from typing import Any

#: Every change implemented so far, in the order they were added. `all` means these.
ADAPTATIONS = ("early_stop",)
#: The SHELDON method name the adapted method is registered under.
METHOD = "sheldon_adapted"


def resolve(names: Any) -> list[str]:
    """The explicit list a config's `adaptations` means: `all` expanded, unknown names
    refused, duplicates dropped, in registry order (so the same set always reads the same)."""
    if isinstance(names, str):
        names = [names]
    given = [str(n).strip() for n in (names or []) if str(n).strip()]
    unknown = [n for n in given if n != "all" and n not in ADAPTATIONS]
    if unknown:
        raise ValueError(f"unknown SHELDON adaptation(s): {', '.join(unknown)}; "
                         f"known: {', '.join(ADAPTATIONS)} (or all)")
    wanted = set(ADAPTATIONS) if "all" in given else set(given)
    return [n for n in ADAPTATIONS if n in wanted]

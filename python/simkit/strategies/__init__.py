"""Phase 3A §4 failure-recovery strategies.

Plugin-discoverable strategies that the orchestrator invokes on per-corner
failures. Built-ins:

  * ``naive_retry`` — re-run failed corners with no intervention (v1).
  * ``gmin_bump``   — ramp Spectre ``gmin`` per-corner (v1.7, DECISIONS #63).
  * ``trans_pss_ic``— inject upstream trans IC on PSS failure
                     (v1.8 #5, on-failure variant of DECISIONS #57 ic_from).

Discovery rule:
  * Built-ins are registered by importing this package.
  * User-defined strategies live in ``<project>/strategies/*.py`` and are
    auto-loaded by ``simkit.strategies.discover()`` at orchestrator startup.
"""

from __future__ import annotations

from typing import Dict, Type

from simkit.strategies.base import Strategy, StrategyContext, StrategyResult
from simkit.strategies.gmin_bump import GminBump
from simkit.strategies.naive_retry import NaiveRetry
from simkit.strategies.trans_pss_ic import TransPssIc


_BUILTINS: Dict[str, Type[Strategy]] = {
    "naive_retry": NaiveRetry,
    "gmin_bump": GminBump,
    "trans_pss_ic": TransPssIc,
}


def get_builtin(name: str) -> Type[Strategy]:
    """Look up a built-in strategy class by name. Raises KeyError if unknown."""
    return _BUILTINS[name]


def builtin_names() -> tuple[str, ...]:
    return tuple(sorted(_BUILTINS.keys()))


__all__ = [
    "Strategy",
    "StrategyContext",
    "StrategyResult",
    "NaiveRetry",
    "GminBump",
    "TransPssIc",
    "get_builtin",
    "builtin_names",
]

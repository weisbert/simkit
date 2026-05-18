"""Phase 3A §4 failure-recovery strategies.

Plugin-discoverable strategies that the orchestrator invokes on per-corner
failures. v1 ships only ``naive_retry`` (DECISIONS #52); ``gmin_bump`` and
``trans_pss_ic`` arrive in v1.1 once the ``asi*`` SKILL surface is probed.

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


_BUILTINS: Dict[str, Type[Strategy]] = {
    "naive_retry": NaiveRetry,
    "gmin_bump": GminBump,
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
    "get_builtin",
    "builtin_names",
]

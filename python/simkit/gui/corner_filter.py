"""Filter matching for the Corner Manager's embedded filter frame.

A :class:`Matcher` pairs a :class:`FilterMode` with a text pattern and an
optional case-sensitivity flag; :meth:`Matcher.matches` is the predicate the
corner table applies to a cell value (a variable / corner name, or a
materialised value). Modes cover text matching (contains, begins / ends with,
word sets, wildcard, regex) plus numeric comparison (``>`` ``<`` ``>=`` ``<=``
``=`` and ranges) for value filtering.

The module is pure — no Qt — so the matching logic is unit-testable on its
own.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class FilterMode(Enum):
    """A text/value match mode. The value is the human-readable menu label."""

    CONTAINS = "Contains"
    EQUALS = "Equals"
    BEGINS_WITH = "Begins with"
    ENDS_WITH = "Ends with"
    ALL_WORDS = "All of the words"
    ANY_WORDS = "Any of the words"
    NONE_WORDS = "None of the words"
    WILDCARD = "Wildcard * ?"
    REGEX = "Regular expression"
    NUMERIC = "Numeric  > < ="

    @property
    def chip(self) -> str:
        """Short always-visible chip text — tells the user the active mode."""
        return _CHIPS[self]


_CHIPS = {
    FilterMode.CONTAINS: "has",
    FilterMode.EQUALS: "=",
    FilterMode.BEGINS_WITH: "a…",
    FilterMode.ENDS_WITH: "…z",
    FilterMode.ALL_WORDS: "&",
    FilterMode.ANY_WORDS: "|",
    FilterMode.NONE_WORDS: "!",
    FilterMode.WILDCARD: "*?",
    FilterMode.REGEX: ".*",
    FilterMode.NUMERIC: "<>",
}

DEFAULT_MODE = FilterMode.CONTAINS

# Menu order — the order the right-click / chip menu lists the modes.
MENU_ORDER: tuple[FilterMode, ...] = (
    FilterMode.CONTAINS,
    FilterMode.EQUALS,
    FilterMode.BEGINS_WITH,
    FilterMode.ENDS_WITH,
    FilterMode.ALL_WORDS,
    FilterMode.ANY_WORDS,
    FilterMode.NONE_WORDS,
    FilterMode.WILDCARD,
    FilterMode.REGEX,
    FilterMode.NUMERIC,
)


@dataclass(frozen=True)
class Matcher:
    """A mode + pattern + case flag — the state of one filter cell."""

    mode: FilterMode = DEFAULT_MODE
    pattern: str = ""
    case_sensitive: bool = False

    @property
    def active(self) -> bool:
        """True when the cell carries a pattern — an empty cell filters
        nothing."""
        return self.pattern.strip() != ""

    def matches(self, value: str) -> bool:
        """True if ``value`` passes this filter. An inactive matcher (empty
        pattern) passes everything."""
        if not self.active:
            return True
        pattern = self.pattern.strip()
        if self.mode is FilterMode.NUMERIC:
            return _numeric_match(pattern, value)
        if self.mode is FilterMode.REGEX:
            return _regex_match(pattern, value, self.case_sensitive)
        hay = value if self.case_sensitive else value.lower()
        needle = pattern if self.case_sensitive else pattern.lower()
        if self.mode is FilterMode.CONTAINS:
            return needle in hay
        if self.mode is FilterMode.EQUALS:
            return hay == needle
        if self.mode is FilterMode.BEGINS_WITH:
            return hay.startswith(needle)
        if self.mode is FilterMode.ENDS_WITH:
            return hay.endswith(needle)
        words = needle.split()
        if self.mode is FilterMode.ALL_WORDS:
            return all(w in hay for w in words)
        if self.mode is FilterMode.ANY_WORDS:
            return any(w in hay for w in words)
        if self.mode is FilterMode.NONE_WORDS:
            return not any(w in hay for w in words)
        if self.mode is FilterMode.WILDCARD:
            return fnmatch.fnmatch(hay, needle)
        return True


def _regex_match(pattern: str, value: str, case_sensitive: bool) -> bool:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        # A half-typed / invalid regex must not blank the table — treat it
        # as "no filter" until it becomes valid.
        return True
    return rx.search(value) is not None


_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _as_float(text: str) -> Optional[float]:
    m = _NUM_RE.fullmatch(text.strip())
    if m is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _numeric_predicate(pattern: str) -> Optional[Callable[[float], bool]]:
    """Parse ``>15`` / ``<=20`` / ``=3`` / ``15..20`` / bare ``15`` into a
    float predicate. Returns None when the pattern is not a numeric query."""
    p = pattern.strip()
    if ".." in p:
        lo_s, _, hi_s = p.partition("..")
        lo, hi = _as_float(lo_s), _as_float(hi_s)
        if lo is None or hi is None:
            return None
        return lambda x: lo <= x <= hi
    for op in (">=", "<=", "==", ">", "<", "="):
        if p.startswith(op):
            num = _as_float(p[len(op):])
            if num is None:
                return None
            if op == ">=":
                return lambda x: x >= num
            if op == "<=":
                return lambda x: x <= num
            if op == ">":
                return lambda x: x > num
            if op == "<":
                return lambda x: x < num
            return lambda x: x == num
    bare = _as_float(p)
    if bare is None:
        return None
    return lambda x: x == bare


def _numeric_match(pattern: str, value: str) -> bool:
    pred = _numeric_predicate(pattern)
    if pred is None:
        # Not a usable numeric query yet — do not filter.
        return True
    num = _as_float(value)
    if num is None:
        # A non-numeric cell can never satisfy a numeric filter.
        return False
    return pred(num)

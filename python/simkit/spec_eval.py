"""v1.4 — Cadence-spec parser + evaluator for the data pillar.

Used by ``pvt ingest`` to compute per-result ``spec_status`` from the spec
string captured by the SKILL collector via ``axlOutputsExportToFile``
(see ``pvtCollect.il``). The ingester denormalises both the spec string
itself and the computed verdict onto every result row so that downstream
queries like ``pvt list --failed-only`` and ``pvt diff --spec-changes`` are
single-table joins.

Forms accepted (Maestro CSV emits these on the read-back direction; the
user-friendly forms are also accepted so bundle-written specs round-trip
through the same parser):

  ============================  =====================  ===============================
  spec string                   meaning                pass criterion
  ============================  =====================  ===============================
  ``"< X"``                     strict upper bound     ``value < X``
  ``"> X"``                     strict lower bound     ``value > X``
  ``"<= X"``                    inclusive upper        ``value <= X``
  ``">= X"``                    inclusive lower        ``value >= X``
  ``"minimize X"``              minimize-with-target   ``value <= X``  (ADE-XL conv.)
  ``"maximize X"``              maximize-with-target   ``value >= X``  (ADE-XL conv.)
  ``"range X Y"``               closed interval        ``X <= value <= Y``
  ``"[X:Y]"``                   alias for range        ``X <= value <= Y``
  ``"X..Y"``                    alias for range        ``X <= value <= Y``
  ``"tolerance X"``             tol-around-target      UNSUPPORTED — verdict ``unsupported``
  ``"tol X"``                   alias for tolerance    UNSUPPORTED
  ============================  =====================  ===============================

``tolerance`` is parsed but the verdict is "unsupported" because the
target value isn't carried in the spec string (Maestro stores it as side
metadata on the rdb, not in the CSV column). v1.5 candidate.

Numeric values accept SI suffixes (``"100p"`` -> 1e-10, ``"2.4G"`` -> 2.4e9)
for round-trip compatibility with user-typed bundle specs. Maestro itself
normalises to scientific notation on CSV output, so live captures should
only ever carry e-notation.

Public API:
    parse_spec(s: str) -> tuple                 — (kind, *values) or raises SpecParseError
    evaluate_spec(spec, value) -> str           — returns enum string spec_status
    SPEC_STATUS_ENUM                            — frozenset of valid enum values
"""

from __future__ import annotations

import math
import re
from typing import Optional, Tuple, Union


SPEC_STATUS_ENUM = frozenset({
    "pass",          # value satisfies the spec
    "fail",          # value violates the spec
    "no_spec",       # output carries no spec
    "no_value",      # value is None / NaN / non-numeric (eval_err, sim_err, ...)
    "unsupported",   # spec form recognised but verdict not computable (tolerance)
    "parse_err",     # spec string couldn't be parsed
})


class SpecParseError(ValueError):
    """Raised when a spec string fails to parse."""


# SI suffix → multiplier. Keys are sorted by length descending in the matcher
# so "Meg" matches before "M".
_SI_MAP = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "m": 1e-3, "k": 1e3, "K": 1e3,
    "M": 1e6, "G": 1e9, "T": 1e12,
    "Meg": 1e6, "MEG": 1e6, "meg": 1e6,
}
# Longer suffixes first so "Meg" wins over "M".
_SI_SUFFIXES_BY_LEN = sorted(_SI_MAP.keys(), key=len, reverse=True)

# Mantissa: optional sign, digits with optional decimal, optional scientific
# exponent. SI suffix tail handled separately so we can give a clearer error.
_NUMBER_RE = re.compile(
    r"\A([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([A-Za-z]*)\Z"
)


def _parse_number(token: str) -> float:
    """Parse a numeric token with optional SI suffix. Raises SpecParseError."""
    if token is None:
        raise SpecParseError("missing number")
    s = token.strip()
    if not s:
        raise SpecParseError("empty number")
    m = _NUMBER_RE.match(s)
    if not m:
        raise SpecParseError(f"not a number: {token!r}")
    mantissa, suffix = m.group(1), m.group(2)
    try:
        value = float(mantissa)
    except ValueError as exc:
        raise SpecParseError(f"bad mantissa: {token!r}") from exc
    if suffix:
        # Match longest suffix; "Meg" before "M".
        for s_key in _SI_SUFFIXES_BY_LEN:
            if suffix == s_key:
                return value * _SI_MAP[s_key]
        raise SpecParseError(f"unknown SI suffix {suffix!r} in {token!r}")
    return value


def parse_spec(spec: str) -> Tuple:
    """Parse a Cadence-style spec string.

    Returns ``(kind, *values)`` where kind is one of
    ``lt / gt / le / ge / minimize / maximize / range / tolerance``.
    Raises :class:`SpecParseError` on bad input.
    """
    if not isinstance(spec, str):
        raise SpecParseError(f"spec must be string, got {type(spec).__name__}")
    s = spec.strip()
    if not s:
        raise SpecParseError("empty spec")

    # Bracket form: "[X:Y]"
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1]
        parts = inner.split(":")
        if len(parts) != 2:
            raise SpecParseError(f"bracket form must be [X:Y]: {spec!r}")
        return ("range", _parse_number(parts[0]), _parse_number(parts[1]))

    # Comparison operators — 2-char first (<=, >=) then 1-char (<, >).
    if s.startswith("<="):
        return ("le", _parse_number(s[2:]))
    if s.startswith(">="):
        return ("ge", _parse_number(s[2:]))
    if s.startswith("<"):
        return ("lt", _parse_number(s[1:]))
    if s.startswith(">"):
        return ("gt", _parse_number(s[1:]))

    # Dotted X..Y range. Reject 3+ consecutive dots and multi-".." (the same
    # guards the SKILL parser uses; see DECISIONS #46).
    if "..." in s:
        raise SpecParseError(
            f"3+ consecutive dots in spec, use 'X..Y' exactly: {spec!r}"
        )
    if ".." in s:
        if s.count("..") > 1:
            raise SpecParseError(
                f"multiple '..' separators in spec: {spec!r}"
            )
        left, right = s.split("..", 1)
        return ("range", _parse_number(left), _parse_number(right))

    # Keyword forms — whitespace-split tokens.
    tokens = s.split()
    head = tokens[0]
    if head == "range" and len(tokens) == 3:
        return ("range", _parse_number(tokens[1]), _parse_number(tokens[2]))
    if head == "minimize" and len(tokens) == 2:
        return ("minimize", _parse_number(tokens[1]))
    if head == "maximize" and len(tokens) == 2:
        return ("maximize", _parse_number(tokens[1]))
    if head == "tolerance":
        # Maestro CSV emits "tolerance X ()" with trailing empty parens
        # representing an optional second value. We accept "tolerance X"
        # and any tail (ignored). Pass/fail not computable in v1.4 — the
        # target value isn't in the CSV column.
        if len(tokens) < 2:
            raise SpecParseError(f"tolerance form needs a value: {spec!r}")
        return ("tolerance", _parse_number(tokens[1]))
    if head == "tol" and len(tokens) == 2:
        return ("tolerance", _parse_number(tokens[1]))

    raise SpecParseError(f"unrecognised spec form: {spec!r}")


def evaluate_spec(spec: Optional[str], value: Union[None, int, float]) -> str:
    """Compute the spec verdict enum for one (spec, value) pair.

    Returns one of :data:`SPEC_STATUS_ENUM`. Never raises.
    """
    if spec is None or (isinstance(spec, str) and spec.strip() == ""):
        return "no_spec"
    if value is None:
        return "no_value"
    if not isinstance(value, (int, float)) or (
        isinstance(value, float) and math.isnan(value)
    ):
        return "no_value"

    try:
        parsed = parse_spec(spec)
    except SpecParseError:
        return "parse_err"

    kind = parsed[0]
    if kind == "lt":
        return "pass" if value < parsed[1] else "fail"
    if kind == "gt":
        return "pass" if value > parsed[1] else "fail"
    if kind == "le":
        return "pass" if value <= parsed[1] else "fail"
    if kind == "ge":
        return "pass" if value >= parsed[1] else "fail"
    if kind == "minimize":
        # ADE-XL convention: minimize-with-target X means pass if value <= X.
        return "pass" if value <= parsed[1] else "fail"
    if kind == "maximize":
        return "pass" if value >= parsed[1] else "fail"
    if kind == "range":
        return "pass" if parsed[1] <= value <= parsed[2] else "fail"
    if kind == "tolerance":
        # Target value isn't in the spec string — Maestro stores it as side
        # metadata that axlSKILL doesn't surface. Mark unsupported until a
        # read accessor materialises (v1.5 candidate).
        return "unsupported"
    # Defensive fallthrough: parse_spec only ever produces the kinds above.
    return "parse_err"

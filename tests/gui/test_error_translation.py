"""Pure-Python tests for :mod:`simkit.gui.error_translation`.

No PyQt5 required — the translation layer is intentionally Qt-free so it
can run inside the headless test matrix without the GUI extras.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.bridge_worker import BridgeError  # noqa: E402
from simkit.gui.error_translation import (  # noqa: E402
    KNOWN_ERRORS,
    TranslatedError,
    translate,
    translate_exception,
)


def _err(category: str, message: str = "", source: str | None = None) -> BridgeError:
    return BridgeError(category=category, message=message, source=source)


# --- Exact-category entries ----------------------------------------------


def test_translate_pvt_runner_no_session_matches():
    out = translate(_err("pvt_runner_no_session", "fnxSession0 missing"))
    assert out.is_known is True
    assert "Maestro session" in out.headline
    assert "fnxSession0" in out.action_hint


def test_translate_lock_failed_matches():
    out = translate(_err("lock_failed", "history not found"))
    assert out.is_known is True
    assert "history lock" in out.headline


def test_translate_pvt_runner_no_option_matches():
    out = translate(_err("pvt_runner_no_option", "skipped"))
    assert out.is_known is True
    assert "Spectre option" in out.headline


def test_translate_pvt_runner_timeout_matches():
    out = translate(_err("pvt_runner_timeout", "did not return"))
    assert out.is_known is True
    assert "timed out" in out.headline


def test_translate_bridge_socket_dead_matches():
    out = translate(_err("bridge_socket_dead", ""))
    assert out.is_known is True
    assert "skillbridge" in out.headline


def test_translate_bridge_dead_matches():
    out = translate(_err("bridge_dead", "server died"))
    assert out.is_known is True
    assert "python_server" in out.headline


def test_translate_bridge_wedge_matches():
    out = translate(_err("bridge_wedge", "stale half-response"))
    assert out.is_known is True
    assert "stuck" in out.headline


def test_translate_session_focus_lost_matches():
    out = translate(_err("session_focus_lost", "active session"))
    assert out.is_known is True
    assert "focus" in out.headline


def test_translate_bad_history_name_matches():
    out = translate(_err("bad_history_name", "newline char"))
    assert out.is_known is True
    assert "history" in out.headline


def test_translate_pvt_io_matches():
    out = translate(_err("pvt_io", "no such file"))
    assert out.is_known is True
    assert "file operation" in out.headline


def test_translate_transport_matches():
    out = translate(_err("transport", "weird shape"))
    assert out.is_known is True
    assert "response format" in out.headline


# --- Substring-priority entries ------------------------------------------


def test_translate_assembler_2423_wins_over_generic_category():
    # pvt_runner_timeout would otherwise match the category-only entry;
    # the ASSEMBLER-2423 substring is higher priority because it's a
    # more specific user-actionable signal.
    out = translate(
        _err("pvt_runner_timeout", "ASSEMBLER-2423: setupdb temporarily locked")
    )
    assert out.is_known is True
    assert "dialog" in out.headline


def test_translate_axl_get_run_status_nil_substring():
    out = translate(_err("anything", "axlGetRunStatus returned nil for fnxSession0"))
    assert out.is_known is True
    assert "session is not recognised" in out.headline


def test_translate_connection_refused_substring():
    out = translate(_err("transport", "Connection refused on socket"))
    # "Connection refused" is listed first; ensure that's the one we hit.
    assert out.is_known is True
    assert "Virtuoso is not running" in out.headline


def test_translate_socket_substring_falls_to_socket_entry():
    out = translate(_err("anything", "the socket was closed unexpectedly"))
    assert out.is_known is True
    assert "Virtuoso is not running" in out.headline


def test_translate_constraint_violation_substring():
    out = translate(_err("duckdb_error", "Constraint violation on PK"))
    assert out.is_known is True
    assert "database" in out.headline


# --- pvt_validation: substring beats bare category ------------------------


def test_translate_pvt_validation_not_found_specific_match():
    out = translate(_err("pvt_validation", "review.json not found"))
    assert out.is_known is True
    assert "cannot be found" in out.headline


def test_translate_pvt_validation_generic_falls_to_validation_entry():
    out = translate(_err("pvt_validation", "session field required"))
    assert out.is_known is True
    assert "validation failed" in out.headline


# --- Fallthrough ---------------------------------------------------------


def test_translate_unknown_category_is_fallthrough():
    out = translate(_err("brand_new_oops", "wow"))
    assert out.is_known is False
    assert "brand_new_oops" in out.headline
    assert "Report this" in out.action_hint


def test_fallthrough_detail_includes_raw_message():
    out = translate(_err("brand_new_oops", "wow what"))
    assert "brand_new_oops" in out.detail
    assert "wow what" in out.detail


def test_detail_includes_source_when_present():
    out = translate(_err("pvt_validation", "boom", source="fnxSession0"))
    assert "source: fnxSession0" in out.detail


def test_detail_omits_source_when_absent():
    out = translate(_err("pvt_validation", "boom"))
    assert "source" not in out.detail


# --- TranslatedError contract --------------------------------------------


def test_translated_error_is_frozen_dataclass():
    out = translate(_err("pvt_validation", ""))
    try:
        out.headline = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("TranslatedError should be frozen / immutable")


def test_translate_never_raises_on_empty_message():
    out = translate(_err("pvt_validation"))
    assert isinstance(out, TranslatedError)


def test_translate_never_raises_on_blank_category():
    out = translate(_err("", "ASSEMBLER-2423"))
    # Blank category still hits the ASSEMBLER-2423 substring entry.
    assert out.is_known is True


# --- translate_exception convenience -------------------------------------


def test_translate_exception_unknown_runtime_error():
    out = translate_exception(RuntimeError("totally novel oops"))
    assert isinstance(out, TranslatedError)
    assert out.is_known is False
    assert "RuntimeError" in out.headline


def test_translate_exception_known_skill_bridge_error():
    from simkit.skill_bridge import SkillBridgeError

    exc = SkillBridgeError("lock_failed", "history not found")
    out = translate_exception(exc)
    assert out.is_known is True
    assert "history lock" in out.headline


# --- Table sanity --------------------------------------------------------


def test_known_errors_table_is_non_empty_and_well_shaped():
    assert len(KNOWN_ERRORS) >= 7
    for entry in KNOWN_ERRORS:
        assert len(entry) == 3
        matcher, headline, hint = entry
        assert isinstance(headline, str) and headline
        assert isinstance(hint, str) and hint
        assert isinstance(matcher, (str, tuple))


def test_every_table_entry_actually_matches_when_probed():
    # Walk the table and synthesize a BridgeError that should hit each.
    for matcher, headline, _hint in KNOWN_ERRORS:
        if isinstance(matcher, str):
            err = _err(matcher, "")
        else:
            cat, substr = matcher
            err = _err(cat or "anything", substr)
        out = translate(err)
        # Either we matched this entry's headline, OR an earlier (higher
        # priority) substring entry pre-empted it. Both are valid.
        assert out.is_known is True

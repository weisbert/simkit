"""Translate :class:`BridgeError` payloads into actionable zh-CN messages.

Spec §8.3 (mandate B5): raw error text like ``ASSEMBLER-2423`` or
``pvt_runner_no_session`` is meaningless to a Chinese-speaking analog IC
engineer. This module maps known ``(category, message-substring)`` pairs
to a one-line headline + concrete next-step hint. Unknown errors fall
through with the raw text + a "Report this" hint.

Pure Python — no Qt import. The Qt-aware bridge into a ``BridgeWorker``
lives in :mod:`simkit.gui.controllers.error_translator`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover - typing only
    from simkit.gui.bridge_worker import BridgeError


@dataclass(frozen=True)
class TranslatedError:
    """User-visible translation of a :class:`BridgeError`."""

    headline: str
    detail: str
    action_hint: str
    is_known: bool


# Matcher forms:
#   str                       -> exact category match
#   (category, substr)        -> category match AND substr in message
#   (None,     substr)        -> substr in message (any category)
Matcher = Union[str, Tuple[Optional[str], str]]


# First match wins; order = priority. Substring matches before bare
# category matches so a more specific reason (e.g. ``ASSEMBLER-2423``
# inside a generic ``pvt_runner_*`` failure) wins.
KNOWN_ERRORS: List[Tuple[Matcher, str, str]] = [
    (
        (None, "ASSEMBLER-2423"),
        "Maestro 当前有对话框打开 (setupdb temporarily locked)",
        "请点击 Maestro 主窗口取消对话框，然后重试。",
    ),
    (
        (None, "axlGetRunStatus returned nil"),
        "Maestro 当前 session 未识别",
        "在 Maestro 窗口里点一次以激活会话，然后重试。",
    ),
    (
        (None, "Connection refused"),
        "Virtuoso 没在运行 / skillbridge server 已断开",
        '在 CIW 重新执行 (pyKillServer)(pyStartServer ?python "/usr/bin/python3")，或重启 Virtuoso。',
    ),
    (
        (None, "socket"),
        "Virtuoso 没在运行 / skillbridge server 已断开",
        '在 CIW 重新执行 (pyKillServer)(pyStartServer ?python "/usr/bin/python3")，或重启 Virtuoso。',
    ),
    (
        (None, "Constraint violation"),
        "本地数据库被并发写入",
        "关闭其它 simkit 实例后重试。",
    ),
    (
        ("pvt_validation", "not found"),
        "输入文件路径找不到",
        "检查 review.json / union.json / bundle.json 的路径。",
    ),
    (
        "pvt_runner_no_session",
        "Maestro session 不存在或拼写错误",
        "确认会话名 (e.g. fnxSession0) 拼写正确，并已在 Maestro 中打开。",
    ),
    (
        "session_focus_lost",
        "Maestro 当前 session 未识别 (focus 已切走)",
        "在 Maestro Assembler 窗口里点一次以重新激活会话，然后重试。",
    ),
    (
        "bridge_socket_dead",
        "Virtuoso 没在运行 / skillbridge server 已断开",
        '在 CIW 重新执行 (pyKillServer)(pyStartServer ?python "/usr/bin/python3")，或重启 Virtuoso。',
    ),
    (
        "bridge_dead",
        "skillbridge python_server 进程已退出",
        "在 shell 杀掉残留的 python_server 进程，然后在 CIW 执行 (pyStartServer)。",
    ),
    (
        "bridge_wedge",
        "skillbridge 通道被卡住 (stale half-response)",
        "在 CIW 执行 (pyKillServer)(pyStartServer)，然后重试。",
    ),
    (
        "lock_failed",
        "Maestro history lock 操作失败",
        "确认 history 名称存在，且 Maestro 没在 cleanup 中。",
    ),
    (
        "pvt_runner_no_option",
        "Spectre 选项未设置 (这通常是正常的)",
        "如果是探测，可忽略；如果是写入，检查 test 名称。",
    ),
    (
        "pvt_runner_timeout",
        "Maestro 运行超时未返回 idle",
        "检查 Maestro 主窗口是否有错误对话框；如必要重启 Spectre。",
    ),
    (
        "pvt_validation",
        "输入参数校验失败",
        "查看 Details 中的具体字段。",
    ),
    (
        "pvt_io",
        "输入/输出文件操作失败",
        "查看 Details 中的路径并确认权限。",
    ),
    (
        "bad_history_name",
        "history 名称含非法字符 (换行/Tab)",
        "改用纯文本 history 名称后重试。",
    ),
    (
        "transport",
        "skillbridge 响应格式异常",
        "通常意味着 Virtuoso 端的 SKILL 没加载完整；重新加载 simkit SKILL 后重试。",
    ),
]


def _format_detail(err: "BridgeError") -> str:
    base = f"[{err.category}] {err.message}"
    if err.source:
        base += f"  (source: {err.source})"
    return base


def _matches(matcher: Matcher, err: "BridgeError") -> bool:
    if isinstance(matcher, str):
        return err.category == matcher
    cat, substr = matcher
    if cat is not None and err.category != cat:
        return False
    return substr in (err.message or "")


def translate(err: "BridgeError") -> TranslatedError:
    """Match ``err`` against :data:`KNOWN_ERRORS`; first match wins.

    Always returns a :class:`TranslatedError`; never raises.
    """
    detail = _format_detail(err)
    for matcher, headline, hint in KNOWN_ERRORS:
        if _matches(matcher, err):
            return TranslatedError(
                headline=headline,
                detail=detail,
                action_hint=hint,
                is_known=True,
            )
    return TranslatedError(
        headline=f"未识别的错误: {err.category}",
        detail=detail,
        action_hint="如果重现，请通过 Report this 反馈具体复现步骤 + Details 文本。",
        is_known=False,
    )


def translate_exception(exc: BaseException) -> TranslatedError:
    """Wrap any exception via :meth:`BridgeError.from_exception` and translate."""
    from simkit.gui.bridge_worker import BridgeError

    return translate(BridgeError.from_exception(exc))

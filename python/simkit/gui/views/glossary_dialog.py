"""Glossary dialog (G-7) — explains simkit's vocabulary for new users.

The backtest found that terms like *review*, *union*, *bundle* and
*session* have no in-app explanation; "review" especially misreads as a
meeting. This dialog is reachable from Help ▸ 术语表 and the same entries
back the per-widget tooltips.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


# (term, one-line definition). Order is roughly "outermost concept first".
GLOSSARY: tuple[tuple[str, str], ...] = (
    ("模块 (module)",
     "一个 .pvtproject 工作区，对应一个被测电路。simkit 里所有 review / "
     "union / bundle / 运行历史都属于某个模块。"),
    ("Bridge",
     "simkit 与 Cadence Virtuoso 之间的 SKILL 通信通道。顶部的状态点显示"
     "它是否连通；断开时 Pull / Run / Apply 不可用。"),
    ("Session",
     "一个已打开的 Maestro 仿真窗口的名字（如 fnxSession0）。每次 Pull / "
     "Run / Apply 都要指明操作哪个 session。"),
    ("Review（评审运行集）",
     "一份 .review.json，把若干 item（测试 + 角组 + 测量包）打包成一次可"
     "重复的批量仿真。注意：不是「开会评审」，而是「一组要跑的东西」。"),
    ("Union（角组 / PVT 网格）",
     "一份 .union.json，列出要扫的工艺 / 电压 / 温度角（process / voltage "
     "/ temperature）。"),
    ("Bundle（测量包）",
     "一份 .measure.json，定义要从仿真结果里取哪些输出量。"),
    ("Template（测量模板）",
     "参数化的测量定义；bundle 通过填入参数来复用同一个模板。"),
    ("Signal group（信号组）",
     "一组命名信号。模板里用 $SIG 占位，由信号组展开成每个信号一行。"),
    ("Raw（原始表达式条目）",
     "bundle 里直接写一条 OCEAN / 计算表达式的条目，不经过模板。"),
    ("Sweep（扫描条目）",
     "bundle 里对某个参数取多个值的条目，展开成多个输出（如 PN_1M / "
     "PN_10M / PN_100M）。"),
)


def glossary_html() -> str:
    """Render :data:`GLOSSARY` as a definition-list HTML fragment."""
    rows = []
    for term, definition in GLOSSARY:
        rows.append(
            f"<p><b>{term}</b><br>"
            f"<span style='color:#444'>{definition}</span></p>"
        )
    return "\n".join(rows)


class GlossaryDialog(QDialog):
    """Read-only dialog listing simkit's core vocabulary."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("simkit — 术语表")
        self.setMinimumSize(560, 480)

        self.browser = QTextBrowser(self)
        self.browser.setObjectName("glossaryBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(glossary_html())

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, stretch=1)
        layout.addWidget(buttons)

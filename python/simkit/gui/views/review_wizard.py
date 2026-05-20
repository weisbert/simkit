"""From-scratch review wizard (spec §14.2) — Tier-1 capability #8.

A four-step :class:`QWizard` for authoring a brand-new ``.review.json``
when there is no existing review to copy-edit:

    Step 1  Project + name
    Step 2  Items (name / tests / union / bundle)
    Step 3  Failure handling (suite default + retry strategy)
    Step 4  Review the assembled JSON, then save

The heavy lifting — the items table, suite-failure controls, dict
assembly and validation — is shared with the copy-edit dialog and lives
in :mod:`simkit.gui.views.review_editor`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from PyQt5.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from simkit.gui.views.review_editor import (
    ReviewItemsTable,
    SuiteFailureControls,
    build_review_dict,
    discover_bundles,
    discover_unions,
    is_valid_review_name,
    validate_review_dict,
)

_REVIEWS_SUBDIR = "reviews"


class _NamePage(QWizardPage):
    """Step 1 — project (fixed) + a unique review name."""

    def __init__(self, wizard: "ReviewWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("Step 1 — Project & name")
        self.setSubTitle(
            "Name the new review. The project is the currently-open module."
        )
        form = QFormLayout(self)
        form.addRow("Project:", QLabel(wizard.project_name))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("letters / digits / _ / - only")
        self.name_edit.textChanged.connect(self.completeChanged)
        form.addRow("Review name:", self.name_edit)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        form.addRow("", self.error_label)

    def isComplete(self) -> bool:  # noqa: N802 (Qt override)
        return is_valid_review_name(self.name_edit.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt override)
        name = self.name_edit.text().strip()
        target = self._wizard.reviews_dir / f"{name}.review.json"
        if target.exists():
            self.error_label.setText(
                f"{target.name} already exists — choose another name."
            )
            return False
        self.error_label.setText("")
        return True


class _ItemsPage(QWizardPage):
    """Step 2 — at least one review item."""

    def __init__(self, wizard: "ReviewWizard") -> None:
        super().__init__()
        self.setTitle("Step 2 — Items")
        self.setSubTitle(
            "Add one item per (tests, union, bundle) grouping. "
            "Items run sequentially."
        )
        layout = QVBoxLayout(self)
        self.items_table = wizard.items_table
        layout.addWidget(self.items_table)
        self.items_table.changed.connect(self.completeChanged)

    def initializePage(self) -> None:  # noqa: N802 (Qt override)
        if self.items_table.row_count() == 0:
            self.items_table.add_item()

    def isComplete(self) -> bool:  # noqa: N802 (Qt override)
        return self.items_table.row_count() > 0


class _FailurePage(QWizardPage):
    """Step 3 — suite-level failure handling."""

    def __init__(self, wizard: "ReviewWizard") -> None:
        super().__init__()
        self.setTitle("Step 3 — Failure handling")
        self.setSubTitle(
            "Suite-wide defaults. Per-item overrides live in the items table."
        )
        layout = QVBoxLayout(self)
        layout.addWidget(wizard.suite_controls)
        layout.addStretch(1)


class _ReviewPage(QWizardPage):
    """Step 4 — preview the assembled JSON, then Finish writes it."""

    def __init__(self, wizard: "ReviewWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("Step 4 — Review & save")
        self.setSubTitle("Confirm the assembled review, then click Finish.")
        layout = QVBoxLayout(self)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

    def initializePage(self) -> None:  # noqa: N802 (Qt override)
        review_dict = self._wizard.build_dict()
        self.preview.setPlainText(json.dumps(review_dict, indent=2))
        self.error_label.setText("")

    def validatePage(self) -> bool:  # noqa: N802 (Qt override)
        return self._wizard.commit(self.error_label)


class ReviewWizard(QWizard):
    """Four-step new-review wizard. :attr:`saved_path` is set on Finish."""

    def __init__(
        self,
        project_root: Path,
        project_name: str,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("simkit — New Review Wizard")
        self.setMinimumSize(680, 540)
        self.project_root = Path(project_root)
        self.project_name = project_name
        self.reviews_dir = self.project_root / _REVIEWS_SUBDIR
        self.saved_path: Optional[Path] = None

        self.items_table = ReviewItemsTable(
            discover_unions(self.project_root),
            discover_bundles(self.project_root),
        )
        self.suite_controls = SuiteFailureControls()

        self._name_page = _NamePage(self)
        self.addPage(self._name_page)
        self.addPage(_ItemsPage(self))
        self.addPage(_FailurePage(self))
        self.addPage(_ReviewPage(self))

    def review_name(self) -> str:
        return self._name_page.name_edit.text().strip()

    def build_dict(self) -> dict[str, Any]:
        return build_review_dict(
            self.review_name(),
            self.project_name,
            self.items_table.to_items(),
            self.suite_controls.to_on_failure(),
        )

    def commit(self, error_label: QLabel) -> bool:
        """Validate + write the review. Returns False (blocks Finish) on error."""
        review_dict = self.build_dict()
        error, _warnings = validate_review_dict(review_dict, self.reviews_dir)
        if error:
            error_label.setText(error)
            return False
        target = self.reviews_dir / f"{self.review_name()}.review.json"
        try:
            self.reviews_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(review_dict, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            error_label.setText(f"Could not write {target}: {exc}")
            return False
        self.saved_path = target
        return True

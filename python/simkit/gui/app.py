"""GUI bootstrap (spec §17, §7.1).

``main(argv)`` is the single entry point invoked by ``pvt gui``. It:

  1. Imports PyQt5 (with a friendly stderr message if missing).
  2. Reads ``~/.simkit/gui_app.json`` and the per-module
     ``.simkit/gui_state.json`` for the last-visited module.
  3. Builds a :class:`MainWindow`.
  4. Spawns a :class:`BridgeWorker` on a dedicated ``QThread`` and wires
     its ``status_changed`` signal to the top-bar dot.
  5. On close, persists the global + per-module state.

This file is the **only** place that actually instantiates PyQt5 widgets
+ threads. ``main_window.py`` / ``bridge_worker.py`` define the classes
but neither runs an event loop. That makes ``main()`` the gate for the
PyQt5 import — ``python -c 'from simkit.gui import app'`` succeeds even
without PyQt5; the friendly error fires when the user runs ``pvt gui``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from simkit.gui import state as app_state_mod
from simkit.gui.module_session import (
    ModuleSession,
    load_session,
    save_session,
)


log = logging.getLogger(__name__)


_PYQT_MISSING_MSG = (
    "pvt gui: PyQt5 is not installed in this Python environment.\n"
    "Install it via one of:\n"
    "  pip install PyQt5==5.15.9 pytest-qt==4.5.0 QtAwesome==1.4.2\n"
    "  pip install 'simkit[gui]'\n"
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the GUI. Returns a process exit code.

    ``argv`` mirrors the spec §17 surface:

      * no extra args         — restore last-visited module
      * ``--module <path>``   — open the given ``.pvtproject`` first
      * ``--safe-mode``       — skip state restore (fresh launch)

    Exit codes (distinct from the 0/1/2/3/7 used by other ``pvt`` subcommands):
      * 4 — PyQt5 is not installed (ModuleNotFoundError)
      * 5 — PyQt5 installed but fails to load (e.g. Cadence Qt5 shadowing
            wheel's bundled Qt5 via LD_LIBRARY_PATH on EDA hosts)
    """
    args = _parse_args(list(argv) if argv is not None else None)

    try:
        from PyQt5.QtCore import QByteArray  # type: ignore[import-not-found]
        from PyQt5.QtWidgets import QApplication  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        sys.stderr.write(_PYQT_MISSING_MSG)
        return 4
    except ImportError as exc:
        sys.stderr.write(
            "pvt gui: PyQt5 is installed but failed to load:\n"
            f"  {exc}\n"
            "\n"
            "Most often: an older Qt5 on LD_LIBRARY_PATH (e.g. Cadence's\n"
            "/software/public/qt/5.15.x_xcb/lib) is shadowing the venv's\n"
            "bundled Qt5. deploy_venv.sh should prepend the wheel's Qt5 lib\n"
            "dir to LD_LIBRARY_PATH at activation — try a fresh:\n"
            "  bash:  source .venv/bin/activate\n"
            "  csh:   source .venv/bin/activate.csh\n"
        )
        return 5

    # PyQt5 is here; safe to import the widget + worker modules.
    from simkit.gui.bridge_worker import (
        BridgeStatus,
        build_bridge,
    )
    from simkit.gui.main_window import MainWindow

    qapp = QApplication.instance() or QApplication(sys.argv[:1])

    # --- state restore -----------------------------------------------
    app_state = (
        app_state_mod.GuiAppState()
        if args.safe_mode
        else app_state_mod.load_app_state()
    )

    module_path: Optional[Path] = None
    if args.module:
        module_path = Path(args.module).expanduser().resolve()
    elif app_state.last_visited and not args.safe_mode:
        candidate = Path(app_state.last_visited)
        if candidate.exists():
            module_path = candidate

    session: Optional[ModuleSession] = None
    if module_path is not None:
        try:
            session = load_session(
                module_path, project_name=module_path.stem,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load module session for %s: %s",
                        module_path, exc)
            session = ModuleSession(project_path=module_path,
                                    project_name=module_path.stem)

    # --- build window -------------------------------------------------
    window = MainWindow()
    if session is not None:
        window.setWindowTitle(f"simkit — {session.project_name or session.project_path.name}")
    if app_state.window_geometry:
        try:
            window.restoreGeometry(
                QByteArray(_b64_to_bytes(app_state.window_geometry))
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("ignoring bad saved geometry: %s", exc)
    if app_state.window_state:
        try:
            window.restoreState(
                QByteArray(_b64_to_bytes(app_state.window_state))
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("ignoring bad saved windowState: %s", exc)

    # --- bridge worker ------------------------------------------------
    thread, worker = build_bridge()
    worker.status_changed.connect(window.set_bridge_status)
    window.set_bridge_status(BridgeStatus.AMBER)
    thread.start()

    # --- shutdown wiring ---------------------------------------------
    def _persist_on_close():
        # Per-module: write the live session back to its project dir.
        if session is not None:
            try:
                save_session(session)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not save module session: %s", exc)

        # Global: update last_visited + recent + geometry.
        if module_path is not None:
            app_state.last_visited = str(module_path)
            app_state.push_recent(str(module_path))
        try:
            app_state.window_geometry = _bytes_to_b64(
                bytes(window.saveGeometry())
            )
            app_state.window_state = _bytes_to_b64(bytes(window.saveState()))
        except Exception as exc:  # noqa: BLE001
            log.debug("could not capture window geometry: %s", exc)
        try:
            app_state_mod.save_app_state(app_state)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save app state: %s", exc)

        # Stop the worker thread cleanly.
        worker.stop()
        thread.quit()
        thread.wait(2000)

    qapp.aboutToQuit.connect(_persist_on_close)

    window.show()
    return int(qapp.exec_())


# --- helpers ---------------------------------------------------------


def _parse_args(argv: Optional[list[str]]):
    """Parse the ``pvt gui`` flags. Kept tiny + dependency-free.

    Mirrors spec §17. Lives here (not in ``cli/gui.py``) so that
    ``main()`` can be called directly with a list of strings, e.g. from a
    test harness or a future ``pvt gui --module ...`` shortcut.
    """
    import argparse

    p = argparse.ArgumentParser(prog="pvt gui", add_help=True)
    p.add_argument(
        "--module", default=None,
        help="Open the GUI directly on this .pvtproject (overrides last-visited).",
    )
    p.add_argument(
        "--safe-mode", action="store_true",
        help="Skip restore of last-visited module + window geometry.",
    )
    return p.parse_args(argv)


def _b64_to_bytes(s: str) -> bytes:
    import base64
    return base64.b64decode(s.encode("ascii"))


def _bytes_to_b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")

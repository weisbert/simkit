"""Right-panel view widgets — one module per tab kind.

Each view is a self-contained ``QWidget`` subclass that the
``MainWindow`` instantiates and adds to its central tab stack. Views
emit signals for any side-effecting action (e.g. ``push_requested``,
``run_requested``) and ``MainWindow`` routes those to the
``BridgeWorker`` queue or to a ``QProcess`` subprocess. Views never
import ``BridgeWorker`` directly — that keeps each view unit-testable
without a Qt event loop and without the threading harness.
"""

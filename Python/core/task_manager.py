"""
core/task_manager.py

Centralized manager for background tasks (QThreads).
Allows tasks to persist across UI tab changes.
"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal, QThread

logger = logging.getLogger(__name__)

class TaskManager(QObject):
    """
    Tracks active background workers. 
    UI tabs register their workers here to ensure they aren't garbage collected
    when a tab is hidden or destroyed.
    """
    task_started = pyqtSignal(str)  # Task name
    task_finished = pyqtSignal(str) # Task name

    def __init__(self):
        super().__init__()
        self._active_tasks: dict[str, QThread] = {}

    def start_task(self, name: str, worker: QThread):
        """Register and start a new background task."""
        if name in self._active_tasks:
            logger.warning(f"Task '{name}' is already running. Skipping.")
            return

        logger.info(f"Starting background task: {name}")
        self._active_tasks[name] = worker
        
        # Ensure clean up when thread finishes
        worker.finished.connect(lambda: self._on_task_finished(name))
        worker.start()
        self.task_started.emit(name)

    def _on_task_finished(self, name: str):
        if name in self._active_tasks:
            logger.info(f"Task finished: {name}")
            del self._active_tasks[name]
            self.task_finished.emit(name)

    def is_running(self, name: str) -> bool:
        return name in self._active_tasks

    def cancel_task(self, name: str):
        if name in self._active_tasks:
            worker = self._active_tasks[name]
            if hasattr(worker, "cancel"):
                worker.cancel()
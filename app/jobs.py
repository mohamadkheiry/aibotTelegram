from __future__ import annotations

import logging
import threading
from collections.abc import Callable


class PeriodicWorker:
    def __init__(
        self,
        callback: Callable[[], None],
        *,
        interval_seconds: float = 10,
        name: str = "periodic-worker",
        logger: logging.Logger | None = None,
    ) -> None:
        self.callback = callback
        self.interval_seconds = interval_seconds
        self.name = name
        self.log = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        # A maintenance cycle may own an in-flight database or network
        # operation.  Keeping this thread non-daemon prevents interpreter
        # teardown from abandoning that operation halfway through a commit.
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=False)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Request shutdown and wait for the current bounded cycle to finish.

        The default intentionally has no shorter, arbitrary join timeout.  The
        callback is responsible for bounding one cycle and for observing its
        application-level stop event between items.  A caller that supplies an
        explicit timeout can still make a non-blocking diagnostic stop.
        """

        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if not thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.callback()
            except Exception:
                self.log.exception("Periodic job failed")
            self._stop.wait(self.interval_seconds)

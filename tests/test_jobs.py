from __future__ import annotations

import threading
import unittest

from app.jobs import PeriodicWorker


class PeriodicWorkerTests(unittest.TestCase):
    def test_worker_runs_and_stops(self) -> None:
        called = threading.Event()
        worker = PeriodicWorker(called.set, interval_seconds=0.01)
        worker.start()
        self.assertTrue(called.wait(1))
        worker.stop()
        self.assertFalse(worker.is_running)

    def test_worker_is_non_daemon_and_stop_joins_the_active_cycle(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def bounded_cycle() -> None:
            entered.set()
            release.wait(1)
            finished.set()

        worker = PeriodicWorker(bounded_cycle, interval_seconds=60)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertIsNotNone(worker._thread)
        self.assertFalse(worker._thread.daemon)  # type: ignore[union-attr]

        stopped = threading.Event()

        def stop_worker() -> None:
            worker.stop()
            stopped.set()

        stopper = threading.Thread(target=stop_worker)
        stopper.start()
        self.assertFalse(stopped.wait(0.05), "stop must join the active callback")
        release.set()
        stopper.join(1)

        self.assertTrue(finished.is_set())
        self.assertTrue(stopped.is_set())
        self.assertFalse(stopper.is_alive())
        self.assertFalse(worker.is_running)
        self.assertFalse(
            any(
                thread.name == "periodic-worker" and thread.is_alive()
                for thread in threading.enumerate()
            )
        )


if __name__ == "__main__":
    unittest.main()

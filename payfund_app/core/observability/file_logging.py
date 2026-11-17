"""Bounded, best-effort JSON file sink. Disk I/O never runs on the caller."""

import logging
import os
import queue
import threading

from payfund_app.core.observability.metrics import TELEMETRY_DROPPED


class _File:
    # Not a logging.Handler: logging.shutdown must never flush disk on its caller.
    def __init__(self, path, max_bytes, backups):
        self.path, self.max_bytes, self.backups = path, max_bytes, backups

    def emit(self, line):
        encoded = (line + "\n").encode("utf-8")
        # A prior crash/ENOSPC may have left an incomplete final JSON line.
        # Discard only that suffix before appending another complete event.
        if os.path.exists(self.path):
            with open(self.path, "r+b") as previous:
                size = previous.seek(0, os.SEEK_END)
                if size:
                    previous.seek(-1, os.SEEK_END)
                    if previous.read(1) != b"\n":
                        start = max(0, size - 65537)
                        previous.seek(start)
                        tail = previous.read()
                        last = tail.rfind(b"\n")
                        previous.truncate(start + last + 1 if last >= 0 else 0)
                        TELEMETRY_DROPPED.labels("logs", "file_incomplete_line").inc()
        if (
            os.path.exists(self.path)
            and os.path.getsize(self.path) + len(encoded) > self.max_bytes
        ):
            for index in range(self.backups, 0, -1):
                source = self.path if index == 1 else f"{self.path}.{index - 1}"
                if os.path.exists(source):
                    os.replace(source, f"{self.path}.{index}")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        with os.fdopen(fd, "ab") as stream:
            stream.write(encoded)

    def close(self):
        pass


class BoundedFileHandler(logging.Handler):
    """One daemon writer, 256 preformatted lines, 1s maximum close wait.

    A nonblocking OS lock prevents independent rotation of the same path. Fixed
    component filenames avoid accumulating orphan files across process restarts.
    """

    def __init__(self, path, *, capacity=256, max_bytes=10 * 1024 * 1024, backups=5):
        super().__init__()
        self.path = os.path.abspath(path)
        self._diddipay_file = path
        self.lines = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self._sink = _File(self.path, max_bytes, backups)
        self._lease = None
        self.thread = threading.Thread(
            target=self._run, name="diddi-json-writer", daemon=True
        )
        self.thread.start()

    def emit(self, record):
        try:
            if self.stopping.is_set():
                self._drop("file_shutdown")
                return
            # Capture context and redact before crossing the thread boundary.
            line = self.format(record)
            if line:
                self.lines.put_nowait(line)
        except queue.Full:
            self._drop("file_queue_full")
        except Exception:  # noqa: BLE001 - serialization must never fail a business call
            self._drop("serialization")

    @staticmethod
    def _drop(reason, count=1):
        TELEMETRY_DROPPED.labels("logs", reason).inc(count)

    def _acquire(self):
        if self._lease is not None:
            return
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o640)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(fd)
            raise
        self._lease = fd

    def _run(self):
        try:
            while not self.stopping.is_set() or not self.lines.empty():
                try:
                    line = self.lines.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    self._acquire()
                    self._sink.emit(line)
                except Exception:  # noqa: BLE001 - isolate all file failures from the producer
                    self._drop("file_write_failed")
                finally:
                    self.lines.task_done()
        finally:
            self._sink.close()
            if self._lease is not None:
                os.close(self._lease)

    def close(self):
        if not self.stopping.is_set():
            self.stopping.set()
            self.thread.join(timeout=1)
            # Pending records cannot be guaranteed after the shutdown deadline.
            while True:
                try:
                    self.lines.get_nowait()
                    self.lines.task_done()
                    self._drop("file_shutdown")
                except queue.Empty:
                    break
        super().close()

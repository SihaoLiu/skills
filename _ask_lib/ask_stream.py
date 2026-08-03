#!/usr/bin/env python3

import os
import queue
import select
import threading
import time
from typing import BinaryIO, Callable, NamedTuple, Optional, Protocol, Sequence, Tuple


RECORD_IO_GRACE_SECONDS = 0.1
RECORD_QUEUE_CHUNKS = 16
OUTPUT_WRITE_CHUNK_BYTES = 4096


class _SignalState(Protocol):
    forwarded_generation: int

    @property
    def forwarded_events(self) -> Sequence[Tuple[int, float]]: ...


class _SignalOutputGrace:
    def __init__(self, duration: float) -> None:
        self.duration = duration
        self.generation = 0
        self.forwarded_at: Optional[float] = None
        self.pending_since: Optional[float] = None
        self.stall_deadline: Optional[float] = None

    def _observe_signal(
        self,
        forwarded_at: float,
        pending: bool,
    ) -> None:
        self.forwarded_at = forwarded_at
        if (
            pending
            and self.stall_deadline is None
            and self.pending_since is not None
            and self.pending_since <= forwarded_at + self.duration
        ):
            self.stall_deadline = (
                max(self.pending_since, forwarded_at) + self.duration
            )

    def should_abandon(
        self,
        relay: _SignalState,
        pending: bool,
        progress: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        current_time = time.monotonic() if now is None else now
        was_pending = self.pending_since is not None
        if pending and self.pending_since is None:
            self.pending_since = current_time
        elif not pending:
            self.pending_since = None
            self.stall_deadline = None
            if was_pending:
                self.forwarded_at = None
        if relay.forwarded_generation != self.generation:
            for generation, forwarded_at in relay.forwarded_events:
                if generation <= self.generation:
                    continue
                self.generation = generation
                self._observe_signal(forwarded_at, pending)
        if progress and pending:
            self.pending_since = current_time
            if self.stall_deadline is not None:
                if (
                    self.forwarded_at is not None
                    and current_time > self.forwarded_at + self.duration
                ):
                    self.forwarded_at = None
                    self.stall_deadline = None
                else:
                    self.stall_deadline = current_time + self.duration
        if self.forwarded_at is None:
            return False
        if not pending:
            if current_time >= self.forwarded_at + self.duration:
                self.forwarded_at = None
            return False
        if self.pending_since is None:
            return False
        if self.stall_deadline is None:
            if self.pending_since > self.forwarded_at + self.duration:
                return False
            self.stall_deadline = (
                max(self.pending_since, self.forwarded_at) + self.duration
            )
        if current_time < self.stall_deadline:
            return False
        self.forwarded_at = None
        self.stall_deadline = None
        return True


class _StreamResult(NamedTuple):
    unforwarded_sigpipe: bool
    terminate_backend: bool


class _OutputWriter:
    def __init__(self, name: str, descriptor: int) -> None:
        self.name = name
        self.descriptor = descriptor
        self.tasks: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=1)
        self.results: queue.Queue[Optional[BaseException]] = queue.Queue(maxsize=1)
        self.notification, self.worker_notification = os.pipe()
        os.set_blocking(self.notification, False)
        self.busy = False
        self.progress_generation = 0
        self.thread = threading.Thread(
            target=self._run,
            name="ask-{}-writer".format(name),
            daemon=True,
        )
        self.thread.start()

    def submit(self, content: bytes) -> None:
        if self.busy:
            raise RuntimeError("{} writer already has pending output".format(self.name))
        self.busy = True
        self.tasks.put_nowait(content)

    def finish(self) -> bool:
        notifications = bytearray()
        try:
            while True:
                content = os.read(self.notification, 4096)
                if not content:
                    break
                notifications.extend(content)
        except BlockingIOError:
            pass
        if b"D" not in notifications:
            return False
        error = self.results.get_nowait()
        self.busy = False
        if error is None:
            return True
        if isinstance(error, OSError):
            detail = error.strerror or str(error)
            raise OSError(error.errno, "{}: {}".format(self.name, detail)) from error
        raise OSError("{} writer failed: {}".format(self.name, error)) from error

    def close(self) -> None:
        try:
            os.close(self.notification)
        except OSError:
            pass
        try:
            self.tasks.put_nowait(None)
        except queue.Full:
            pass
        if not self.busy:
            self.thread.join()

    def _run(self) -> None:
        try:
            while True:
                content = self.tasks.get()
                if content is None:
                    return
                error = None
                try:
                    remaining = memoryview(content)
                    while remaining:
                        try:
                            written = os.write(
                                self.descriptor,
                                remaining[:OUTPUT_WRITE_CHUNK_BYTES],
                            )
                        except BlockingIOError:
                            select.select([], [self.descriptor], [])
                            continue
                        if written <= 0:
                            raise OSError("terminal writer made no progress")
                        remaining = remaining[written:]
                        self.progress_generation += 1
                        if remaining:
                            os.write(self.worker_notification, b"P")
                except BaseException as caught:
                    error = caught
                self.results.put(error)
                try:
                    os.write(self.worker_notification, b"D")
                except OSError:
                    return
        finally:
            try:
                os.close(self.worker_notification)
            except OSError:
                pass


class _RecordWriter:
    def __init__(
        self,
        name: str,
        log: BinaryIO,
        write_chunk: Callable[[BinaryIO, bytes], None],
        warning: Callable[[str], None],
    ) -> None:
        self.name = name
        self.log = log
        self.write_chunk = write_chunk
        self.warning = warning
        self.tasks: queue.Queue[Optional[bytes]] = queue.Queue(
            maxsize=RECORD_QUEUE_CHUNKS
        )
        self.condition = threading.Condition()
        self.finished = threading.Event()
        self.completed = 0
        self.accepting = True
        self.stop_sent = False
        self.warned = False
        self.thread = threading.Thread(
            target=self._run,
            name="ask-{}-record-writer".format(name),
            daemon=True,
        )
        self.thread.start()

    def submit(self, content: bytes) -> bool:
        should_warn = False
        with self.condition:
            if not self.accepting:
                return False
            deadline = time.monotonic() + RECORD_IO_GRACE_SECONDS
            while True:
                try:
                    self.tasks.put_nowait(content)
                    return True
                except queue.Full:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.accepting = False
                        should_warn = self._mark_warned_locked()
                        break
                    self.condition.wait(timeout=remaining)
        if should_warn:
            self._warn_slow_record()
        return False

    def close(self) -> None:
        with self.condition:
            if self.accepting:
                self.accepting = False
                self._request_stop_locked()
            completed = self.completed
            deadline = time.monotonic() + RECORD_IO_GRACE_SECONDS
            while not self.finished.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(timeout=remaining)
                if self.completed != completed:
                    completed = self.completed
                    deadline = time.monotonic() + RECORD_IO_GRACE_SECONDS
            finished = self.finished.is_set()
            if not finished:
                should_warn = self._mark_warned_locked()
            else:
                should_warn = False
        if finished:
            self.thread.join()
        elif should_warn:
            self._warn_slow_record()

    def _request_stop_locked(self) -> None:
        if not self.stop_sent:
            try:
                self.tasks.put_nowait(None)
            except queue.Full:
                return
            self.stop_sent = True

    def _mark_warned_locked(self) -> bool:
        if self.warned:
            return False
        self.warned = True
        return True

    def _warn_slow_record(self) -> None:
        self.warning(
            "backend {} record storage is blocked; recording stopped".format(self.name)
        )

    def _run(self) -> None:
        try:
            while True:
                content = self.tasks.get()
                if content is None:
                    return
                try:
                    self.write_chunk(self.log, content)
                except BaseException as error:
                    self.warning(
                        "cannot write backend {} record: {}".format(
                            self.name,
                            error,
                        )
                    )
                    with self.condition:
                        self.accepting = False
                        self.condition.notify_all()
                    return
                with self.condition:
                    self.completed += 1
                    self.condition.notify_all()
                    if not self.accepting and self.tasks.empty():
                        return
        finally:
            try:
                self.log.close()
            except OSError as error:
                self.warning(
                    "cannot close backend {} record: {}".format(
                        self.name,
                        error,
                    )
                )
            finally:
                self.finished.set()
                with self.condition:
                    self.condition.notify_all()

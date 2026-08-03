#!/usr/bin/env python3

import ctypes
import os
import signal
import sys
from typing import Iterable, Optional, Sequence

try:
    import resource as _resource
except ImportError:
    _resource = None  # type: ignore[assignment]


GROUP_SENTINEL_ARGUMENT = "--group-sentinel"
PR_SET_PDEATHSIG = 1
WATCHDOG_ARM_COMMAND = b"A"
WATCHDOG_KILL_COMMAND = b"K"
WATCHDOG_TERMINATE_COMMAND = b"T"

if sys.platform.startswith("linux"):
    _LIBC = ctypes.CDLL(None, use_errno=True)
else:
    _LIBC = None


def _ignore_catchable_signals() -> None:
    for number in signal.valid_signals():
        if number in (signal.SIGKILL, signal.SIGSTOP):
            continue
        try:
            signal.signal(number, signal.SIG_IGN)
        except (OSError, RuntimeError, ValueError):
            continue


def _spawn_group_sentinel(
    watchdog_descriptor: int,
    liveness_descriptor: int,
) -> None:
    read_descriptor, write_descriptor = os.pipe()
    try:
        helper_pid = os.fork()
    except BaseException:
        os.close(read_descriptor)
        os.close(write_descriptor)
        raise

    if helper_pid == 0:
        os.close(read_descriptor)
        try:
            sentinel_pid = os.fork()
            if sentinel_pid != 0:
                os._exit(0)
            _ignore_catchable_signals()
            null_descriptor = os.open(os.devnull, os.O_RDWR)
            try:
                for descriptor in (0, 1, 2):
                    os.dup2(null_descriptor, descriptor)
            finally:
                if null_descriptor > 2:
                    os.close(null_descriptor)
            _close_descriptors_except(
                (
                    0,
                    1,
                    2,
                    write_descriptor,
                    watchdog_descriptor,
                    liveness_descriptor,
                )
            )
            for descriptor in (
                write_descriptor,
                watchdog_descriptor,
                liveness_descriptor,
            ):
                os.set_inheritable(descriptor, True)
            executable = os.path.abspath(sys.executable)
            os.execve(
                executable,
                [
                    executable,
                    "-S",
                    os.path.abspath(__file__),
                    GROUP_SENTINEL_ARGUMENT,
                    str(write_descriptor),
                    str(watchdog_descriptor),
                    str(liveness_descriptor),
                ],
                {},
            )
        except BaseException:
            os._exit(126)

    os.close(write_descriptor)
    try:
        while True:
            try:
                _, helper_status = os.waitpid(helper_pid, 0)
                break
            except InterruptedError:
                continue
        ready = os.read(read_descriptor, 1)
    finally:
        os.close(read_descriptor)
    if os.waitstatus_to_exitcode(helper_status) != 0 or ready != b"R":
        raise OSError("backend process-group sentinel did not start")


def _prepare_backend(
    parent_pid: int,
    watchdog_descriptor: Optional[int],
    liveness_descriptor: Optional[int],
    ignore_sigchld: bool = False,
) -> None:
    if _LIBC is not None:
        _LIBC.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGKILL)

    if watchdog_descriptor is not None:
        if liveness_descriptor is None:
            os._exit(126)
        try:
            message = "{}\n".format(os.getpid()).encode("ascii")
            remaining = memoryview(message)
            while remaining:
                written = os.write(watchdog_descriptor, remaining)
                if written <= 0:
                    os._exit(126)
                remaining = remaining[written:]
        except OSError:
            os._exit(126)
        try:
            _spawn_group_sentinel(watchdog_descriptor, liveness_descriptor)
        finally:
            for descriptor in (watchdog_descriptor, liveness_descriptor):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    if ignore_sigchld:
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)


def _watchdog_entry(descriptor: int, liveness_writer: int) -> None:
    try:
        try:
            os.setsid()
        except OSError:
            pass

        message = b""
        while b"\n" not in message:
            chunk = os.read(descriptor, 64)
            if not chunk:
                return
            message += chunk

        pid_text, pending = message.split(b"\n", 1)
        process_group = int(pid_text)
        armed = False
        kill_requested = False
        terminate_requested = False
        while True:
            for command in pending:
                if command == WATCHDOG_ARM_COMMAND[0]:
                    armed = True
                elif command == WATCHDOG_KILL_COMMAND[0]:
                    kill_requested = True
                elif command == WATCHDOG_TERMINATE_COMMAND[0]:
                    terminate_requested = True
            if armed and terminate_requested:
                try:
                    os.killpg(process_group, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                terminate_requested = False
            if armed and kill_requested:
                break
            pending = os.read(descriptor, 64)
            if not pending:
                if not armed:
                    return
                break
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    except (OSError, ValueError):
        pass
    finally:
        for owned_descriptor in (descriptor, liveness_writer):
            try:
                os.close(owned_descriptor)
            except OSError:
                pass


def _close_descriptors_except(preserved: Iterable[int]) -> None:
    keep = set(preserved)
    descriptors: Iterable[int]
    for descriptor_directory in ("/proc/self/fd", "/dev/fd"):
        try:
            descriptors = [
                int(name)
                for name in os.listdir(descriptor_directory)
                if name.isdecimal()
            ]
        except (OSError, ValueError):
            continue
        break
    else:
        limits = []
        try:
            current_limit = int(os.sysconf("SC_OPEN_MAX"))
            if current_limit > 0:
                limits.append(current_limit)
        except (OSError, ValueError):
            pass
        if _resource is not None:
            try:
                _soft_limit, hard_limit = _resource.getrlimit(
                    _resource.RLIMIT_NOFILE
                )
                if hard_limit != _resource.RLIM_INFINITY and hard_limit > 0:
                    limits.append(int(hard_limit))
            except (OSError, ValueError):
                pass
        descriptors = range(max(limits, default=256))

    for descriptor in descriptors:
        if descriptor in keep:
            continue
        try:
            os.close(descriptor)
        except OSError:
            pass


def _group_sentinel_main(
    ready_descriptor: int,
    watchdog_descriptor: int,
    liveness_descriptor: int,
) -> None:
    _ignore_catchable_signals()
    try:
        if os.write(watchdog_descriptor, WATCHDOG_ARM_COMMAND) != len(
            WATCHDOG_ARM_COMMAND
        ):
            raise OSError("watchdog arming made no progress")
    finally:
        os.close(watchdog_descriptor)
    try:
        try:
            os.write(ready_descriptor, b"R")
        except OSError:
            pass
    finally:
        os.close(ready_descriptor)
    try:
        while os.read(liveness_descriptor, 1):
            pass
    except OSError:
        pass
    finally:
        try:
            os.close(liveness_descriptor)
        except OSError:
            pass
    # The watchdog owns the only writer, so EOF means supervision was lost.
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 4 or arguments[0] != GROUP_SENTINEL_ARGUMENT:
        return 2
    try:
        descriptors = tuple(int(argument) for argument in arguments[1:])
    except ValueError:
        return 2
    _group_sentinel_main(*descriptors)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

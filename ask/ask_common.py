#!/usr/bin/env python3

import errno
import functools
import hashlib
import os
import secrets
import selectors
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple, cast

from ask_request import InvocationError, MAX_ARGUMENT_BYTES, Request, parse_request
from ask_stream import (
    _OutputWriter,
    _RecordWriter,
    _SignalOutputGrace,
    _StreamResult,
)
from ask_progress import StructuredOutput
from ask_watchdog import (
    WATCHDOG_KILL_COMMAND,
    WATCHDOG_TERMINATE_COMMAND,
    _close_descriptors_except,
    _prepare_backend,
    _watchdog_entry,
)


ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_KEY_FILENAME = "zai.key"

CLAUDE_MODEL = "claude-opus-5[1m]"
CODEX_MODEL = "gpt-5.6-sol"
KIMI_MODEL = "kimi-code/k3"
GLM_MODEL = "glm-5.2[1m]"

RECORD_ROOT_VARIABLE = "ASK_AI_HOME"
DEFAULT_RECORD_ROOT = "~/.ask-ai"
REDACTED = "<redacted>"
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

STREAM_CHUNK_BYTES = 65536
STREAM_SELECT_SECONDS = 0.05
STREAM_DRAIN_GRACE_SECONDS = 2.0
SIGNAL_OUTPUT_GRACE_SECONDS = 0.5
FORWARDED_SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
    signal.SIGQUIT,
)
_NONREAPING_WAIT_SUPPORTED = os.name == "posix" and all(
    hasattr(os, name)
    for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "waitid")
)


class EnvironmentAssignment(NamedTuple):
    value: str
    secret_source: Optional[Path] = None


class EnvironmentChanges(NamedTuple):
    assigned: Dict[str, EnvironmentAssignment]
    cleared: Tuple[str, ...]

    def apply(self, environ: Mapping[str, str]) -> Dict[str, str]:
        result = dict(environ)
        for name in self.cleared:
            result.pop(name, None)
        result.update(
            (name, assignment.value) for name, assignment in self.assigned.items()
        )
        return result


class Watchdog(NamedTuple):
    pid: int
    descriptor: int
    liveness_descriptor: int


class RecordDirectory(NamedTuple):
    path: Path
    descriptor: int


class Invocation(NamedTuple):
    command: List[str]
    environment: EnvironmentChanges
    harness: str
    model: str
    prompt: str
    stdin: Optional[bytes] = None


def _claude_command(
    model: str,
    session_id: Optional[str],
    text_output: bool,
    policy_text: Optional[str],
) -> List[str]:
    command = ["claude", "--safe-mode", "-p"]
    if session_id:
        command.extend(["--resume", session_id])
    command.extend(
        [
            "--model",
            model,
            "--effort",
            "max",
            "--permission-mode",
            "auto",
            "--output-format",
            "text" if text_output else "stream-json",
            "--verbose",
        ]
    )
    if not text_output:
        command.extend(
            ["--include-partial-messages", "--forward-subagent-text"]
        )
    if policy_text:
        command.extend(["--append-system-prompt", policy_text])
    return command


def _prompt_with_policy(prompt: str, policy_text: Optional[str]) -> str:
    if policy_text:
        return "{}\n\n# Task\n{}".format(policy_text, prompt)
    return prompt


def _codex_command(
    session_id: Optional[str],
    text_output: bool,
) -> List[str]:
    command = ["codex", "exec"]
    if session_id:
        command.append("resume")
    command.extend(
        [
            "--model",
            CODEX_MODEL,
            "--config",
            'model_reasoning_effort="max"',
        ]
    )
    if not text_output:
        command.append("--json")
    if session_id:
        command.append(session_id)
    command.extend(["--", "-"])
    return command


def _read_zai_key_file(key_path: Path) -> str:
    try:
        content = key_path.read_bytes()
    except OSError as error:
        raise InvocationError("cannot read Z.AI key file {}: {}".format(key_path, error))

    if content.endswith(b"\n"):
        content = content[:-1]
    try:
        key = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvocationError(
            "Z.AI key has invalid format; expected {key-id}.{secret}"
        ) from error
    parts = key.split(".")
    invalid_character = any(
        char.isspace() or not char.isprintable() for char in key
    )
    if len(parts) != 2 or not all(parts) or invalid_character:
        raise InvocationError("Z.AI key has invalid format; expected {key-id}.{secret}")
    return key


def _read_zai_key(environ: Mapping[str, str]) -> Tuple[str, Path]:
    secret_root = environ.get("PERSONAL_SECRET_PATH")
    if not secret_root:
        raise InvocationError("PERSONAL_SECRET_PATH is not set")

    try:
        key_path = (Path(secret_root).expanduser() / ZAI_KEY_FILENAME).resolve()
    except (OSError, RuntimeError) as error:
        raise InvocationError(
            "cannot resolve Z.AI key path {}: {}".format(secret_root, error)
        ) from error
    return _read_zai_key_file(key_path), key_path


def _glm_environment(environ: Mapping[str, str]) -> EnvironmentChanges:
    key, key_path = _read_zai_key(environ)
    return EnvironmentChanges(
        assigned={
            "ANTHROPIC_AUTH_TOKEN": EnvironmentAssignment(
                value=key,
                secret_source=key_path,
            ),
            "ANTHROPIC_BASE_URL": EnvironmentAssignment(ZAI_BASE_URL),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": EnvironmentAssignment("glm-4.5-air"),
            "ANTHROPIC_DEFAULT_OPUS_MODEL": EnvironmentAssignment(GLM_MODEL),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": EnvironmentAssignment(GLM_MODEL),
            "API_TIMEOUT_MS": EnvironmentAssignment("3000000"),
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": EnvironmentAssignment("1000000"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": EnvironmentAssignment("1"),
        },
        cleared=(
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_CUSTOM_HEADERS",
            "ANTHROPIC_DEFAULT_FABLE_MODEL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_SMALL_FAST_MODEL",
            "CLAUDE_CODE_AUTO_MODE_MODEL",
            "CLAUDE_CODE_BG_CLASSIFIER_MODEL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CODE_USE_ANTHROPIC_AWS",
            "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_USE_GATEWAY",
            "CLAUDE_CODE_USE_MANTLE",
            "CLAUDE_CODE_USE_VERTEX",
        ),
    )


def build_invocation(
    backend: str,
    prompt: str,
    session_id: Optional[str],
    text_output: bool,
    policy_text: Optional[str],
    environ: Optional[Mapping[str, str]] = None,
) -> Invocation:
    if not prompt.strip():
        raise InvocationError("prompt cannot be empty")
    if "\x00" in prompt or (policy_text is not None and "\x00" in policy_text):
        raise InvocationError("prompt and policy cannot contain NUL")
    if (
        backend in ("claude", "glm")
        and policy_text is not None
        and len(_encode(policy_text)) > MAX_ARGUMENT_BYTES
    ):
        raise InvocationError("policy is too large for a command argument")
    parent_environment = os.environ if environ is None else environ
    environment = EnvironmentChanges(assigned={}, cleared=())
    delivered_prompt = prompt
    stdin_payload: Optional[bytes] = None

    if backend == "kimi":
        model = KIMI_MODEL
        environment = EnvironmentChanges(
            assigned={
                "KIMI_MODEL_THINKING_EFFORT": EnvironmentAssignment("max")
            },
            cleared=(),
        )
        delivered_prompt = _prompt_with_policy(prompt, policy_text)
        if len(_encode(delivered_prompt)) > MAX_ARGUMENT_BYTES:
            raise InvocationError("Kimi prompt is too large for a command argument")
        command = ["kimi"]
        if session_id:
            command.extend(["-S", session_id])
        command.extend(
            [
                "-m",
                KIMI_MODEL,
                "-p",
                delivered_prompt,
                "--output-format",
                "text" if text_output else "stream-json",
            ]
        )
    elif backend == "claude":
        model = CLAUDE_MODEL
        stdin_payload = _encode(prompt)
        command = _claude_command(
            model=CLAUDE_MODEL,
            session_id=session_id,
            text_output=text_output,
            policy_text=policy_text,
        )
    elif backend == "codex":
        model = CODEX_MODEL
        delivered_prompt = _prompt_with_policy(prompt, policy_text)
        stdin_payload = _encode(delivered_prompt)
        command = _codex_command(
            session_id=session_id,
            text_output=text_output,
        )
    elif backend == "glm":
        model = GLM_MODEL
        environment = _glm_environment(parent_environment)
        stdin_payload = _encode(prompt)
        command = _claude_command(
            model="opus",
            session_id=session_id,
            text_output=text_output,
            policy_text=policy_text,
        )
    else:
        raise InvocationError("unknown backend: {}".format(backend))

    return Invocation(
        command=command,
        environment=environment,
        harness=command[0],
        model=model,
        prompt=delivered_prompt,
        stdin=stdin_payload,
    )


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _toml_string(value: str) -> str:
    """Render a TOML basic string.

    TOML is defined over UTF-8, so bytes that are not valid UTF-8 cannot survive
    here. They are shown as replacement characters; prompt.md keeps the exact
    bytes and prompt_sha256 covers them.
    """
    characters = []
    for character in value:
        escape = _TOML_ESCAPES.get(character)
        if escape is not None:
            characters.append(escape)
        elif "\ud800" <= character <= "\udfff":
            characters.append("\ufffd")
        elif character < " " or character == "\x7f":
            characters.append("\\u{:04X}".format(ord(character)))
        else:
            characters.append(character)
    return '"{}"'.format("".join(characters))


def _toml_key(name: str) -> str:
    bare = name and all(
        character.isascii() and (character.isalnum() or character in "_-") for character in name
    )
    return name if bare else _toml_string(name)


def _toml_array(values: Iterable[str]) -> str:
    return "[{}]".format(", ".join(_toml_string(value) for value in values))


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _encode(text: str) -> bytes:
    """Bytes as the backend receives them.

    Arguments arrive from argv through surrogateescape, so a prompt that is not
    valid UTF-8 must round-trip back to its original bytes rather than fail.
    """
    return text.encode("utf-8", "surrogateescape")


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


def _path_slug(text: str) -> str:
    """Fold an identifier into one filesystem path component.

    Model names carry separators and brackets -- kimi-code/k3 would otherwise
    grow a directory level, and claude-opus-5[1m] would need quoting everywhere.
    """
    characters = [
        character if character.isascii() and (character.isalnum() or character in "._-") else "-"
        for character in text
    ]
    slug = "".join(characters)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-._") or "unknown"


def _record_root(environ: Mapping[str, str]) -> Path:
    configured = environ.get(RECORD_ROOT_VARIABLE)
    root = Path(configured or DEFAULT_RECORD_ROOT).expanduser()
    return Path(os.path.abspath(root))


def _directory_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _open_directory_at(name: str, parent_descriptor: int) -> int:
    descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise NotADirectoryError("record path component is not a directory: {}".format(name))
    return descriptor


def _create_record_directory(
    root: Path,
    harness: str,
    model: str,
    started_at: datetime,
) -> RecordDirectory:
    created_root = False
    try:
        root.mkdir(parents=True, mode=0o700)
        created_root = True
    except FileExistsError:
        pass
    root_status = root.lstat()
    if stat.S_ISLNK(root_status.st_mode):
        raise PermissionError("record root cannot be a symbolic link: {}".format(root))
    if not stat.S_ISDIR(root_status.st_mode):
        raise NotADirectoryError("record root is not a directory: {}".format(root))

    opened_descriptors: List[int] = []
    run_descriptor: Optional[int] = None
    try:
        root_descriptor = os.open(str(root), _directory_flags())
        opened_descriptors.append(root_descriptor)
        if created_root:
            os.fchmod(root_descriptor, 0o700)
        opened_status = os.fstat(root_descriptor)
        if (root_status.st_dev, root_status.st_ino) != (
            opened_status.st_dev,
            opened_status.st_ino,
        ):
            raise PermissionError("record root changed while it was opened: {}".format(root))
        root_mode = stat.S_IMODE(opened_status.st_mode)
        if root_mode != 0o700:
            raise PermissionError(
                "record root {} has mode {:04o}; expected 0700".format(root, root_mode)
            )

        components = (
            _path_slug(harness),
            _path_slug(model),
            started_at.strftime("%Y%m%d"),
        )
        current_descriptor = root_descriptor
        for component in components:
            created = False
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_descriptor)
                created = True
            except FileExistsError:
                pass
            child_descriptor = _open_directory_at(component, current_descriptor)
            opened_descriptors.append(child_descriptor)
            if created:
                os.fchmod(child_descriptor, 0o700)
            current_descriptor = child_descriptor

        directory_name: Optional[str] = None
        for _ in range(8):
            candidate = "{}-{}".format(
                started_at.strftime("%H%M%S"),
                secrets.token_hex(4),
            )
            try:
                os.mkdir(candidate, mode=0o700, dir_fd=current_descriptor)
            except FileExistsError:
                continue
            directory_name = candidate
            run_descriptor = _open_directory_at(candidate, current_descriptor)
            os.fchmod(run_descriptor, 0o700)
            break
        if directory_name is None or run_descriptor is None:
            raise OSError("cannot allocate a unique record directory under {}".format(root))

        current_root = root.lstat()
        if stat.S_ISLNK(current_root.st_mode) or (
            current_root.st_dev,
            current_root.st_ino,
        ) != (opened_status.st_dev, opened_status.st_ino):
            raise PermissionError("record root changed during creation: {}".format(root))

        directory = root.joinpath(*components, directory_name)
        result = RecordDirectory(path=directory, descriptor=run_descriptor)
        run_descriptor = None
        return result
    finally:
        if run_descriptor is not None:
            os.close(run_descriptor)
        for descriptor in reversed(opened_descriptors):
            os.close(descriptor)


def _close_record(record: RecordDirectory) -> None:
    try:
        os.close(record.descriptor)
    except OSError:
        pass


def _write_private(
    record: RecordDirectory,
    name: str,
    content: bytes,
    mode: int = 0o600,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, mode, dir_fd=record.descriptor)
    try:
        os.fchmod(descriptor, mode)
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("record writer made no progress")
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def _configuration_document(
    directory: Path,
    backend: str,
    request: Request,
    invocation: Invocation,
    executable: str,
    resolved_executable: Optional[str],
    started_at: datetime,
) -> str:
    prompt = _encode(invocation.prompt)

    lines = [
        "# ask invocation record",
        "",
        "[invocation]",
        "id = {}".format(_toml_string(directory.name)),
        "backend = {}".format(_toml_string(backend)),
        "harness = {}".format(_toml_string(invocation.harness)),
        "model = {}".format(_toml_string(invocation.model)),
        "started_at = {}".format(_toml_string(started_at.isoformat(timespec="seconds"))),
        "cwd = {}".format(_toml_string(str(request.cwd))),
        "text_output = {}".format(_toml_bool(request.text_output)),
        "progress_output = {}".format(_toml_bool(request.progress_output)),
    ]
    if request.session_id:
        lines.append("session_id = {}".format(_toml_string(request.session_id)))
    if request.policy_file:
        lines.append("policy_file = {}".format(_toml_string(request.policy_file)))
        lines.append(
            "policy_inlined = {}".format(_toml_bool(invocation.prompt != request.prompt))
        )

    lines.extend(
        [
            "",
            "[request]",
            "wrapper = {}".format(_toml_string(_wrapper_path(backend))),
            "argv = {}".format(_toml_array(request.argv)),
            "prompt_from_stdin = {}".format(_toml_bool(request.prompt_from_stdin)),
            "prompt_bytes = {}".format(len(prompt)),
            "prompt_sha256 = {}".format(_toml_string(hashlib.sha256(prompt).hexdigest())),
            "",
            "[command]",
            "executable = {}".format(_toml_string(executable)),
        ]
    )
    if resolved_executable is not None:
        lines.append(
            "resolved_executable = {}".format(_toml_string(resolved_executable))
        )
    lines.extend(
        [
            "argv = {}".format(_toml_array(invocation.command)),
            "",
            "[environment]",
            "cleared = {}".format(_toml_array(invocation.environment.cleared)),
            "",
            "[environment.set]",
        ]
    )
    for name in sorted(invocation.environment.assigned):
        assignment = invocation.environment.assigned[name]
        value = (
            REDACTED
            if assignment.secret_source is not None or _is_secret_name(name)
            else assignment.value
        )
        lines.append("{} = {}".format(_toml_key(name), _toml_string(value)))
    lines.append("")
    return "\n".join(lines)


def _reproduce_script(
    directory: Path,
    backend: str,
    request: Request,
    invocation: Invocation,
    executable: str,
    started_at: datetime,
) -> str:
    lines = [
        "#!/bin/sh",
        "# ask record {}".format(directory.name),
        "# backend {} -- harness {} -- model {}".format(
            backend, invocation.harness, invocation.model
        ),
        "# recorded {}".format(started_at.isoformat(timespec="seconds")),
        "",
        "set +x",
        "set -eu",
        "",
        "cd {}".format(shlex.quote(str(request.cwd))),
    ]
    if invocation.environment.cleared:
        lines.append("unset {}".format(" ".join(invocation.environment.cleared)))
    for name in sorted(invocation.environment.assigned):
        assignment = invocation.environment.assigned[name]
        if assignment.secret_source is not None:
            source = shlex.quote(str(assignment.secret_source))
            lines.extend(
                [
                    "{}=$(".format(name),
                    "    {} {} --emit-zai-key {}".format(
                        shlex.quote(os.path.abspath(sys.executable)),
                        shlex.quote(os.path.abspath(__file__)),
                        source,
                    ),
                    ")",
                ]
            )
            lines.append("export {}".format(name))
        elif _is_secret_name(name):
            lines.append(
                "# {} held a secret; export it yourself before running this script".format(name)
            )
        else:
            lines.append(
                "export {}={}".format(
                    name, shlex.quote(assignment.value)
                )
            )

    arguments = [shlex.quote(executable)]
    arguments.extend(shlex.quote(argument) for argument in invocation.command[1:])
    if invocation.stdin is not None:
        stdin_redirect = " < {}".format(
            shlex.quote(str(directory / "prompt.md"))
        )
    elif request.prompt_from_stdin:
        stdin_redirect = " < /dev/null"
    else:
        stdin_redirect = ""
    lines.extend(
        ["", "exec {}{}".format(" ".join(arguments), stdin_redirect), ""]
    )
    return "\n".join(lines)


def _wrapper_path(backend: str) -> str:
    entrypoint = sys.argv[0] if sys.argv and sys.argv[0] else "ask-{}".format(backend)
    try:
        return str(Path(entrypoint).resolve())
    except OSError:
        return entrypoint


def _send_socket_diagnostic(descriptor: int, content: bytes) -> None:
    duplicate = os.dup(descriptor)
    try:
        sender = socket.socket(fileno=duplicate)
    except BaseException:
        os.close(duplicate)
        raise
    flags = socket.MSG_DONTWAIT | getattr(socket, "MSG_NOSIGNAL", 0)
    try:
        remaining = memoryview(content)
        while remaining:
            written = sender.send(remaining, flags)
            if written <= 0:
                return
            remaining = remaining[written:]
    finally:
        sender.close()


def _diagnostic(message: str) -> None:
    owned_descriptor: Optional[int] = None
    try:
        descriptor = sys.stderr.fileno()
        blocking = os.get_blocking(descriptor)
        content = _encode(message + "\n")
        if blocking and sys.platform.startswith("linux"):
            mode = os.fstat(descriptor).st_mode
            if stat.S_ISSOCK(mode):
                _send_socket_diagnostic(descriptor, content)
                return
            if not stat.S_ISREG(mode):
                flags = os.O_WRONLY | os.O_NONBLOCK
                if hasattr(os, "O_CLOEXEC"):
                    flags |= os.O_CLOEXEC
                try:
                    owned_descriptor = os.open(
                        "/proc/self/fd/{}".format(descriptor),
                        flags,
                    )
                except OSError:
                    return
                descriptor = owned_descriptor
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                return
            remaining = remaining[written:]
    except (AttributeError, OSError, ValueError):
        pass
    finally:
        if owned_descriptor is not None:
            try:
                os.close(owned_descriptor)
            except OSError:
                pass


def _open_record(
    backend: str,
    request: Request,
    invocation: Invocation,
    executable: Optional[str],
    resolved_executable: Optional[str],
    parent_environment: Mapping[str, str],
    started_at: datetime,
) -> Optional[RecordDirectory]:
    """Create this run's record directory and write everything known up front.

    Recording is a secondary duty: when the record cannot be written the run
    still goes ahead, with a warning, rather than failing over bookkeeping.
    """
    recorded_executable = executable or invocation.command[0]
    record: Optional[RecordDirectory] = None
    try:
        record = _create_record_directory(
            root=_record_root(parent_environment),
            harness=invocation.harness,
            model=invocation.model,
            started_at=started_at,
        )
        _write_private(
            record,
            "config.toml",
            _encode(
                _configuration_document(
                    record.path,
                    backend,
                    request,
                    invocation,
                    recorded_executable,
                    resolved_executable,
                    started_at,
                )
            ),
        )
        _write_private(record, "prompt.md", _encode(invocation.prompt))
        _write_private(
            record,
            "reproduce.cmd",
            _encode(
                _reproduce_script(
                    record.path,
                    backend,
                    request,
                    invocation,
                    recorded_executable,
                    started_at,
                )
            ),
            mode=0o700,
        )
        # Every record has the same shape, whether the backend spoke, stayed
        # silent, or never started at all.
        _write_private(record, "stdout", b"")
        _write_private(record, "stderr", b"")
    except (OSError, RuntimeError, ValueError) as error:
        if record is not None:
            _close_record(record)
        _diagnostic(
            "ask-{}: warning: cannot record this invocation: {}".format(backend, error)
        )
        return None

    _diagnostic(
        "Hint: please check {} for in-progress output.".format(
            record.path / "stderr"
        )
    )
    return record


def _append_result(
    record: Optional[RecordDirectory],
    started: float,
    exit_code: Optional[int] = None,
    signal_number: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    if record is None:
        return
    lines = [
        "",
        "[result]",
        "finished_at = {}".format(
            _toml_string(datetime.now().astimezone().isoformat(timespec="seconds"))
        ),
        "duration_seconds = {:.3f}".format(time.monotonic() - started),
    ]
    if exit_code is not None:
        lines.append("exit_code = {}".format(exit_code))
    if signal_number is not None:
        lines.append("signal = {}".format(signal_number))
    if error is not None:
        lines.append("error = {}".format(_toml_string(error)))
    lines.append("")
    try:
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            "config.toml",
            flags,
            dir_fd=record.descriptor,
        )
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(_encode("\n".join(lines)))
    except OSError as error:
        _record_warning("cannot append result to {}: {}".format(record.path, error))
    finally:
        _close_record(record)


def _record_warning(message: str) -> None:
    _diagnostic("ask: warning: {}".format(message))


def _open_logs(record: Optional[RecordDirectory]) -> Dict[str, BinaryIO]:
    if record is None:
        return {}
    logs: Dict[str, BinaryIO] = {}
    for name in ("stdout", "stderr"):
        try:
            flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                name,
                flags,
                dir_fd=record.descriptor,
            )
            logs[name] = os.fdopen(descriptor, "wb", buffering=0)
        except OSError as error:
            _record_warning("cannot open {}: {}".format(record.path / name, error))
            continue
    return logs


def _close_logs(logs: Mapping[str, BinaryIO]) -> None:
    for name, log in logs.items():
        try:
            log.close()
        except OSError as error:
            _record_warning("cannot close backend {} record: {}".format(name, error))
            continue


def _signal_backend_group(process: subprocess.Popen, number: int) -> bool:
    try:
        os.killpg(process.pid, number)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class _LeaderState(NamedTuple):
    exited: bool
    identity_pinned: bool


def _leader_state(process: subprocess.Popen) -> _LeaderState:
    if process.returncode is not None:
        return _LeaderState(exited=True, identity_pinned=False)
    if not _NONREAPING_WAIT_SUPPORTED:
        exited = process.poll() is not None
        return _LeaderState(exited=exited, identity_pinned=not exited)
    while True:
        try:
            result = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except InterruptedError:
            continue
        except ChildProcessError:
            return _LeaderState(exited=True, identity_pinned=False)
        return _LeaderState(exited=result is not None, identity_pinned=True)


def _process_exited(process: subprocess.Popen) -> bool:
    return _leader_state(process).exited


def _leader_identity_pinned(process: subprocess.Popen) -> bool:
    return _leader_state(process).identity_pinned


def _wait_for_process_exit(process: subprocess.Popen) -> None:
    if process.returncode is not None:
        return
    if not _NONREAPING_WAIT_SUPPORTED:
        process.wait()
        return
    while True:
        try:
            os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            return
        except InterruptedError:
            continue
        except ChildProcessError:
            return


class _SignalRelay:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.pending: List[int] = []
        self.unforwarded: List[int] = []
        self.forwarded_generation = 0
        self.forwarded_events: List[Tuple[int, float]] = []

    def __call__(self, number: int, _frame: object) -> None:
        if self.process is None:
            self.pending.append(number)
            return
        self._send(number)

    def attach(self, process: subprocess.Popen) -> None:
        self.process = process
        for number in self.pending:
            self._send(number)

    def _send(self, number: int) -> None:
        if self.process is None:
            return
        leader_exited = _process_exited(self.process)
        if leader_exited:
            return
        delivered = _signal_backend_group(self.process, number)
        if delivered:
            self.forwarded_generation += 1
            forwarded_at = time.monotonic()
            self.forwarded_events.append(
                (self.forwarded_generation, forwarded_at)
            )
        elif _process_exited(self.process):
            return
        else:
            self.unforwarded.append(number)

    def discard_forwarded_events_through(self, generation: int) -> None:
        count = 0
        for event_generation, _forwarded_at in self.forwarded_events:
            if event_generation > generation:
                break
            count += 1
        if count:
            del self.forwarded_events[:count]


def _start_watchdog() -> Optional[Watchdog]:
    if os.name != "posix" or not hasattr(os, "fork"):
        return None
    try:
        read_descriptor, write_descriptor = os.pipe()
        try:
            liveness_descriptor, liveness_writer = os.pipe()
        except OSError:
            os.close(read_descriptor)
            os.close(write_descriptor)
            raise
    except OSError as error:
        _record_warning("cannot start process watchdog: {}".format(error))
        return None
    try:
        pid = os.fork()
    except OSError as error:
        for descriptor in (
            read_descriptor,
            write_descriptor,
            liveness_descriptor,
            liveness_writer,
        ):
            os.close(descriptor)
        _record_warning("cannot start process watchdog: {}".format(error))
        return None

    if pid == 0:
        os.close(write_descriptor)
        os.close(liveness_descriptor)
        _close_descriptors_except((read_descriptor, liveness_writer))
        _watchdog_entry(read_descriptor, liveness_writer)
        os._exit(0)

    os.close(read_descriptor)
    os.close(liveness_writer)
    return Watchdog(
        pid=pid,
        descriptor=write_descriptor,
        liveness_descriptor=liveness_descriptor,
    )


def _stop_watchdog(watchdog: Optional[Watchdog]) -> bool:
    if watchdog is None:
        return False
    command_sent = _send_watchdog_command(watchdog, WATCHDOG_KILL_COMMAND)
    try:
        os.close(watchdog.descriptor)
    except OSError:
        pass
    try:
        os.close(watchdog.liveness_descriptor)
    except OSError:
        pass
    while True:
        try:
            os.waitpid(watchdog.pid, 0)
            return command_sent
        except InterruptedError:
            continue
        except ChildProcessError:
            return command_sent


def _send_watchdog_command(watchdog: Optional[Watchdog], command: bytes) -> bool:
    if watchdog is None:
        return False
    try:
        return os.write(watchdog.descriptor, command) == len(command)
    except OSError:
        return False


def _forward_signals(relay: _SignalRelay) -> Dict[int, Any]:
    previous: Dict[int, Any] = {}

    try:
        for number in FORWARDED_SIGNALS:
            try:
                handler = signal.getsignal(number)
                if handler == signal.SIG_IGN:
                    continue
                previous[number] = handler
                signal.signal(number, relay)
            except (OSError, ValueError):
                previous.pop(number, None)
                continue
    except BaseException:
        _restore_signals(previous)
        raise
    return previous


def _restore_signals(previous: Mapping[int, Any]) -> None:
    for number, handler in previous.items():
        try:
            signal.signal(number, handler)
        except (OSError, ValueError):
            continue


def _write_record_chunk(log: BinaryIO, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = log.write(remaining)
        if written is None or written <= 0:
            raise OSError("record writer made no progress")
        remaining = remaining[written:]


def _stream(
    process: subprocess.Popen,
    logs: Dict[str, BinaryIO],
    relay: Optional[_SignalRelay] = None,
    stdin_payload: Optional[bytes] = None,
    watchdog: Optional[Watchdog] = None,
    structured_output: Optional[StructuredOutput] = None,
) -> _StreamResult:
    remaining_logs: Dict[str, BinaryIO] = {}
    record_writers: Dict[str, _RecordWriter] = {}
    if process.stdout is None or process.stderr is None:
        raise OSError(errno.EBADF, "backend output pipes are unavailable")
    backend_streams: Dict[str, BinaryIO] = {
        "stdout": cast(BinaryIO, process.stdout),
        "stderr": cast(BinaryIO, process.stderr),
    }
    terminal_streams: Dict[str, BinaryIO] = {}
    for name, text_stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        if text_stream is None:
            raise OSError(errno.EBADF, "{} is unavailable".format(name))
        binary_stream = getattr(text_stream, "buffer", None)
        if binary_stream is None:
            raise OSError(errno.EBADF, "{} has no binary stream".format(name))
        terminal_streams[name] = binary_stream
    writers: Dict[str, _OutputWriter] = {}
    writer_progress = {"stdout": 0, "stderr": 0}
    writer_registered = {"stdout": False, "stderr": False}
    pending = {"stdout": False, "stderr": False}
    backend_open = {"stdout": True, "stderr": True}
    backend_registered = {"stdout": False, "stderr": False}
    selector = selectors.DefaultSelector()
    leader_exited_at: Optional[float] = None
    signal_output_graces = {
        name: _SignalOutputGrace(SIGNAL_OUTPUT_GRACE_SECONDS)
        for name in backend_streams
    }
    sigpipe_delivered = False
    unforwarded_sigpipe = False
    terminate_backend = False
    input_stream = process.stdin
    input_offset = 0
    input_open = input_stream is not None

    def terminal_destination(name: str) -> Optional[str]:
        if name == "stderr" or structured_output is not None:
            return None
        return name

    def record_destination(name: str) -> Optional[str]:
        if name == "stdout" and structured_output is not None:
            return "stderr" if structured_output.transcript_output else None
        return name

    def unregister(file_object) -> None:
        try:
            selector.unregister(file_object)
        except (KeyError, ValueError):
            pass

    def register_backend(name: str) -> None:
        destination = terminal_destination(name)
        if (
            backend_open[name]
            and not backend_registered[name]
            and (destination is None or not pending[destination])
        ):
            stream = backend_streams[name]
            selector.register(stream, selectors.EVENT_READ, ("backend", name))
            backend_registered[name] = True

    def close_input() -> None:
        nonlocal input_open
        if not input_open or input_stream is None:
            return
        unregister(input_stream)
        try:
            input_stream.close()
        except OSError:
            pass
        input_open = False

    def close_backend(name: str) -> None:
        if backend_registered[name]:
            unregister(backend_streams[name])
            backend_registered[name] = False
        if backend_open[name]:
            try:
                backend_streams[name].close()
            except OSError:
                pass
            backend_open[name] = False

    def unregister_writer(name: str) -> None:
        if writer_registered[name]:
            unregister(writers[name].notification)
            writer_registered[name] = False

    def register_writer(name: str) -> None:
        if not writer_registered[name]:
            selector.register(
                writers[name].notification,
                selectors.EVENT_READ,
                ("terminal", name),
            )
            writer_registered[name] = True

    def downstream_closed(name: str) -> None:
        nonlocal sigpipe_delivered, unforwarded_sigpipe
        unregister_writer(name)
        pending[name] = False
        for source in backend_streams:
            if terminal_destination(source) == name:
                close_backend(source)
        if sigpipe_delivered:
            return
        sigpipe_delivered = True
        if _process_exited(process) or not _signal_backend_group(
            process, signal.SIGPIPE
        ):
            unforwarded_sigpipe = True

    def finish_terminal(name: str) -> None:
        try:
            completed = writers[name].finish()
        except OSError as error:
            if error.errno == errno.EPIPE:
                downstream_closed(name)
                return
            raise
        if not completed:
            return
        unregister_writer(name)
        pending[name] = False
        for source in backend_streams:
            register_backend(source)

    def submit_record(name: str, content: bytes) -> None:
        if not content:
            return
        record_writer = record_writers.get(name)
        if record_writer is not None and not record_writer.submit(content):
            record_writers.pop(name, None)

    def submit_terminal(name: str, content: bytes) -> None:
        if not content:
            return
        if pending[name]:
            raise RuntimeError("{} writer already has pending output".format(name))
        for source in backend_streams:
            if (
                terminal_destination(source) == name
                and backend_registered[source]
            ):
                unregister(backend_streams[source])
                backend_registered[source] = False
        pending[name] = True
        writers[name].submit(content)
        register_writer(name)

    def abandon_backpressured_output() -> None:
        for name in ("stdout", "stderr"):
            unregister_writer(name)
            pending[name] = False
            close_backend(name)

    def signal_interrupted_backpressure() -> bool:
        if relay is None:
            return False
        abandon = False
        for name in pending:
            progress_generation = writers[name].progress_generation
            made_progress = progress_generation != writer_progress[name]
            writer_progress[name] = progress_generation
            if signal_output_graces[name].should_abandon(
                relay,
                pending=pending[name],
                progress=made_progress,
            ):
                abandon = True
        relay.discard_forwarded_events_through(
            min(grace.generation for grace in signal_output_graces.values())
        )
        return abandon

    try:
        remaining_logs.update(logs)
        logs.clear()
        for name in tuple(remaining_logs):
            new_record_writer = _RecordWriter(
                name,
                remaining_logs[name],
                _write_record_chunk,
                _record_warning,
            )
            record_writers[name] = new_record_writer
            remaining_logs.pop(name)
        for name, terminal in terminal_streams.items():
            descriptor = terminal.fileno()
            try:
                os.get_blocking(descriptor)
            except OSError as error:
                detail = error.strerror or str(error)
                raise OSError(error.errno, "{}: {}".format(name, detail)) from error
            writer = _OutputWriter(name, descriptor)
            writers[name] = writer
        for name in backend_streams:
            register_backend(name)
        if input_open and input_stream is not None:
            if stdin_payload:
                os.set_blocking(input_stream.fileno(), False)
                selector.register(
                    input_stream,
                    selectors.EVENT_WRITE,
                    ("backend_input", "stdin"),
                )
            else:
                close_input()

        while True:
            if not selector.get_map():
                if not _process_exited(process):
                    time.sleep(STREAM_SELECT_SECONDS)
                    continue
                if leader_exited_at is None:
                    leader_exited_at = time.monotonic()
                    close_input()
                    _send_watchdog_command(
                        watchdog,
                        WATCHDOG_TERMINATE_COMMAND,
                    )
                break
            if signal_interrupted_backpressure():
                terminate_backend = True
                abandon_backpressured_output()
                break

            for key, _ in selector.select(STREAM_SELECT_SECONDS):
                kind, name = key.data
                if kind == "terminal":
                    finish_terminal(name)
                    continue
                if kind == "backend_input":
                    if stdin_payload is None:
                        close_input()
                        continue
                    try:
                        written = os.write(
                            key.fd,
                            stdin_payload[
                                input_offset : input_offset + STREAM_CHUNK_BYTES
                            ],
                        )
                    except BrokenPipeError:
                        close_input()
                        continue
                    except BlockingIOError:
                        continue
                    input_offset += written
                    if input_offset == len(stdin_payload):
                        close_input()
                    continue

                if not backend_registered[name]:
                    continue
                chunk = os.read(key.fd, STREAM_CHUNK_BYTES)
                if not chunk:
                    close_backend(name)
                    if name == "stdout" and structured_output is not None:
                        final_output = structured_output.finish()
                        submit_record("stdout", final_output)
                        submit_terminal("stdout", final_output)
                    continue
                if name == "stdout" and structured_output is not None:
                    structured_output.feed(chunk)
                recorded_as = record_destination(name)
                if recorded_as is not None:
                    submit_record(recorded_as, chunk)
                displayed_as = terminal_destination(name)
                if displayed_as is not None:
                    submit_terminal(displayed_as, chunk)

            if signal_interrupted_backpressure():
                terminate_backend = True
                abandon_backpressured_output()
                break

            if not _process_exited(process):
                continue
            now = time.monotonic()
            if leader_exited_at is None:
                leader_exited_at = now
                close_input()
                _send_watchdog_command(
                    watchdog,
                    WATCHDOG_TERMINATE_COMMAND,
                )
            elif now - leader_exited_at >= STREAM_DRAIN_GRACE_SECONDS:
                abandon_backpressured_output()
                break
    finally:
        close_input()
        for name in writers:
            unregister_writer(name)
        for name in backend_streams:
            close_backend(name)
        selector.close()
        for output_writer in writers.values():
            output_writer.close()
        for record_writer in record_writers.values():
            record_writer.close()
        _close_logs(remaining_logs)
    return _StreamResult(
        unforwarded_sigpipe=unforwarded_sigpipe,
        terminate_backend=terminate_backend,
    )


def _execute(
    invocation: Invocation,
    executable: str,
    environment: Mapping[str, str],
    cwd: Path,
    directory: Optional[RecordDirectory],
    structured_output: Optional[StructuredOutput] = None,
) -> int:
    """Run the backend and write its displayed streams to the caller and record.

    Returns the raw wait status: negative when the backend died from a signal.
    """
    logs = _open_logs(directory)
    relay = _SignalRelay()
    previous_sigchld: Any = None
    owns_sigchld = False
    ignore_backend_sigchld = False
    previous_signals: Dict[int, Any] = {}
    watchdog: Optional[Watchdog] = None
    process: Optional[subprocess.Popen] = None
    status: Optional[int] = None
    launch_error: Optional[BaseException] = None
    stream_result = _StreamResult(False, False)
    group_termination_requested = False
    try:
        if hasattr(signal, "SIGCHLD"):
            previous_sigchld = signal.getsignal(signal.SIGCHLD)
            owns_sigchld = True
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            ignore_backend_sigchld = previous_sigchld == signal.SIG_IGN
        previous_signals = _forward_signals(relay)
        watchdog = _start_watchdog()
        if os.name == "posix" and watchdog is None:
            launch_error = subprocess.SubprocessError(
                "process watchdog is unavailable"
            )
        parent_pid = os.getpid()
        watchdog_descriptor = watchdog.descriptor if watchdog is not None else None
        liveness_descriptor = (
            watchdog.liveness_descriptor if watchdog is not None else None
        )
        prepare_backend = (
            functools.partial(
                _prepare_backend,
                parent_pid,
                watchdog_descriptor,
                liveness_descriptor,
                ignore_backend_sigchld,
            )
            if os.name == "posix"
            else None
        )
        pass_fds = (
            (watchdog_descriptor, liveness_descriptor)
            if watchdog_descriptor is not None and liveness_descriptor is not None
            else ()
        )
        if launch_error is None:
            try:
                process = subprocess.Popen(
                    invocation.command,
                    executable=executable,
                    env=environment,
                    cwd=str(cwd),
                    stdin=subprocess.PIPE if invocation.stdin is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    preexec_fn=prepare_backend,
                    pass_fds=pass_fds,
                )
            except (OSError, subprocess.SubprocessError) as error:
                launch_error = error
        if process is not None:
            relay.attach(process)
            stream_result = _stream(
                process,
                logs,
                relay,
                invocation.stdin,
                watchdog,
                structured_output,
            )
            if stream_result.terminate_backend:
                killed_by_watchdog = _stop_watchdog(watchdog)
                watchdog = None
                if not killed_by_watchdog and _leader_identity_pinned(process):
                    _signal_backend_group(process, signal.SIGKILL)
                group_termination_requested = True
            _wait_for_process_exit(process)
    finally:
        try:
            try:
                if process is not None:
                    if not group_termination_requested:
                        killed_by_watchdog = _stop_watchdog(watchdog)
                        watchdog = None
                        if not killed_by_watchdog and _leader_identity_pinned(process):
                            _signal_backend_group(process, signal.SIGKILL)
                    _wait_for_process_exit(process)
                else:
                    _stop_watchdog(watchdog)
            finally:
                _close_logs(logs)
        finally:
            try:
                _restore_signals(previous_signals)
            finally:
                try:
                    if process is not None:
                        status = process.wait()
                finally:
                    if owns_sigchld:
                        signal.signal(signal.SIGCHLD, previous_sigchld)

    if stream_result.unforwarded_sigpipe and status == 0:
        status = -int(signal.SIGPIPE)

    if process is None and relay.pending:
        return -relay.pending[0]
    if relay.unforwarded:
        return -relay.unforwarded[0]
    if launch_error is not None:
        raise launch_error
    if status is None:
        raise subprocess.SubprocessError("backend execution ended without a status")
    return status


def main(backend: str, argv: Optional[Sequence[str]] = None) -> int:
    started = time.monotonic()
    started_at = datetime.now().astimezone()
    parent_environment = dict(os.environ)
    directory: Optional[RecordDirectory] = None

    try:
        request = parse_request(backend, argv)
        if not request.cwd.is_dir():
            raise InvocationError("working directory does not exist: {}".format(request.cwd))
        invocation = build_invocation(
            backend=backend,
            prompt=request.prompt,
            session_id=request.session_id,
            text_output=request.text_output,
            policy_text=request.policy_text,
            environ=parent_environment,
        )
        child_environment = invocation.environment.apply(parent_environment)
        executable = shutil.which(
            invocation.command[0], path=child_environment.get("PATH")
        )
        resolved_executable: Optional[str] = None
        if executable is not None:
            executable = os.path.abspath(executable)
            resolved_executable = str(Path(executable).resolve())
            invocation = invocation._replace(
                command=[executable] + invocation.command[1:]
            )
        directory = _open_record(
            backend=backend,
            request=request,
            invocation=invocation,
            executable=executable,
            resolved_executable=resolved_executable,
            parent_environment=parent_environment,
            started_at=started_at,
        )
        if executable is None:
            raise FileNotFoundError(
                errno.ENOENT,
                "executable is not available: {}".format(invocation.harness),
            )

        status = _execute(
            invocation=invocation,
            executable=executable,
            environment=child_environment,
            cwd=request.cwd,
            directory=directory,
            structured_output=(
                StructuredOutput(backend, request.progress_output)
                if not request.text_output
                else None
            ),
        )
    except KeyboardInterrupt:
        exit_code = 128 + int(signal.SIGINT)
        _append_result(
            directory,
            started,
            exit_code=exit_code,
            signal_number=int(signal.SIGINT),
        )
        return exit_code
    except InvocationError as error:
        _append_result(directory, started, exit_code=2, error=str(error))
        _diagnostic("ask-{}: error: {}".format(backend, error))
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        _append_result(directory, started, exit_code=126, error=str(error))
        _diagnostic("ask-{}: error: {}".format(backend, error))
        return 126

    if status < 0:
        _append_result(directory, started, exit_code=128 - status, signal_number=-status)
        return 128 - status
    _append_result(directory, started, exit_code=status)
    return status


def _secret_replay_main(arguments: Sequence[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "--emit-zai-key":
        _diagnostic("invalid secret source")
        return 2
    try:
        key = _read_zai_key_file(Path(arguments[1]))
        remaining = memoryview(_encode(key))
        while remaining:
            written = os.write(sys.stdout.fileno(), remaining)
            if written <= 0:
                raise OSError("secret writer made no progress")
            remaining = remaining[written:]
    except (InvocationError, OSError, ValueError):
        _diagnostic("invalid secret source")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_secret_replay_main(sys.argv[1:]))

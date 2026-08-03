#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, TextIO, Tuple


MAX_ARGUMENT_BYTES = 131071


class InvocationError(RuntimeError):
    pass


class Request(NamedTuple):
    cwd: Path
    session_id: Optional[str]
    text_output: bool
    policy_file: Optional[str]
    policy_text: Optional[str]
    argv: Tuple[str, ...]
    prompt: str
    prompt_from_stdin: bool
    progress_output: bool = True


def _read_bounded_policy_text(
    policy_file: TextIO,
    maximum_bytes: int,
    path: Path,
) -> str:
    content: List[str] = []
    content_bytes = 0
    pending_whitespace: List[str] = []
    pending_bytes = 0
    pending_overflow = False

    while True:
        chunk = policy_file.read(65536)
        if not chunk:
            return "".join(content)
        for character in chunk:
            character_bytes = len(character.encode("utf-8"))
            if character.isspace():
                if content and not pending_overflow:
                    if content_bytes + pending_bytes + character_bytes <= maximum_bytes:
                        pending_whitespace.append(character)
                        pending_bytes += character_bytes
                    else:
                        pending_overflow = True
                continue
            if (
                pending_overflow
                or content_bytes + pending_bytes + character_bytes > maximum_bytes
            ):
                raise InvocationError(
                    "policy is too large for a command argument: {}".format(path)
                )
            content.extend(pending_whitespace)
            content.append(character)
            content_bytes += pending_bytes + character_bytes
            pending_whitespace.clear()
            pending_bytes = 0


def _read_policy(
    path_text: Optional[str],
    maximum_bytes: Optional[int] = None,
) -> Optional[str]:
    if path_text is None:
        return None
    try:
        path = Path(path_text).expanduser()
    except (OSError, RuntimeError) as error:
        raise InvocationError(
            "cannot resolve policy file {}: {}".format(path_text, error)
        ) from error
    try:
        with path.open("r", encoding="utf-8") as policy_file:
            policy = (
                policy_file.read().strip()
                if maximum_bytes is None
                else _read_bounded_policy_text(policy_file, maximum_bytes, path)
            )
    except (OSError, UnicodeError) as error:
        raise InvocationError("cannot read policy file {}: {}".format(path, error))
    if not policy:
        raise InvocationError("policy file is empty: {}".format(path))
    if "\x00" in policy:
        raise InvocationError("policy file contains NUL: {}".format(path))
    return policy


def _request_argv(arguments: Sequence[str]) -> Tuple[str, ...]:
    value_options = ("--cwd", "--session", "--policy-file", "--progress")
    flag_options = ("--text",)
    result: List[str] = []
    index = 0
    options_enabled = True

    while index < len(arguments):
        argument = arguments[index]
        if options_enabled and argument == "--":
            result.append(argument)
            options_enabled = False
            index += 1
            continue
        if not options_enabled:
            index += 1
            continue
        if argument in ("-C", "-S"):
            result.extend(arguments[index : index + 2])
            index += 2
            continue
        if (argument.startswith("-C") or argument.startswith("-S")) and len(argument) > 2:
            result.append(argument)
            index += 1
            continue
        if argument.startswith("--"):
            name, separator, _value = argument.partition("=")
            matches = [
                option
                for option in value_options + flag_options
                if option.startswith(name)
            ]
            if len(matches) == 1:
                result.append(argument)
                if matches[0] in value_options and not separator:
                    result.extend(arguments[index + 1 : index + 2])
                    index += 2
                else:
                    index += 1
                continue
        index += 1

    return tuple(result)


def parse_request(
    backend: str,
    argv: Optional[Sequence[str]] = None,
    stdin: TextIO = sys.stdin,
) -> Request:
    parser = argparse.ArgumentParser(
        prog="ask-{}".format(backend),
        description="Run the {} coding backend non-interactively.".format(backend),
    )
    parser.add_argument(
        "-C",
        "--cwd",
        default=str(Path.cwd()),
        help="working directory for the coding backend (default: current directory)",
    )
    parser.add_argument(
        "-S",
        "--session",
        dest="session_id",
        help="resume an exact backend session ID",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="use backend-native text passthrough instead of a structured transcript",
    )
    parser.add_argument(
        "--progress",
        choices=("stderr", "off"),
        default="stderr",
        help=(
            "record structured events in the invocation record; "
            "off disables them (default: stderr)"
        ),
    )
    parser.add_argument(
        "--policy-file",
        help="inject an engineering policy file into the request",
    )
    parser.add_argument("prompt", nargs="*", help="prompt text; reads stdin when omitted")
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)

    prompt_from_stdin = not args.prompt
    if prompt_from_stdin:
        binary_stdin = getattr(stdin, "buffer", None)
        if binary_stdin is None:
            prompt = stdin.read().strip()
        else:
            prompt = binary_stdin.read().decode("utf-8", "surrogateescape").strip()
    else:
        prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise InvocationError("prompt must be provided as arguments or stdin")
    if "\x00" in prompt:
        raise InvocationError("prompt cannot contain NUL")

    try:
        cwd = Path(args.cwd).expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise InvocationError(
            "cannot resolve working directory {}: {}".format(args.cwd, error)
        ) from error

    return Request(
        cwd=cwd,
        session_id=args.session_id,
        text_output=args.text,
        policy_file=args.policy_file,
        policy_text=_read_policy(
            args.policy_file,
            MAX_ARGUMENT_BYTES if backend in ("claude", "glm", "kimi") else None,
        ),
        argv=_request_argv(arguments),
        prompt=prompt,
        prompt_from_stdin=prompt_from_stdin,
        progress_output=args.progress == "stderr",
    )

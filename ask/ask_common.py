#!/usr/bin/env python3

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, TextIO


ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
GLM_MODEL = "glm-5.2[1m]"


class InvocationError(RuntimeError):
    pass


class Invocation(NamedTuple):
    command: List[str]
    environment: Dict[str, str]


class Request(NamedTuple):
    cwd: Path
    session_id: Optional[str]
    text_output: bool
    policy_text: Optional[str]
    prompt: str


def _read_policy(path_text: Optional[str]) -> Optional[str]:
    if path_text is None:
        return None
    path = Path(path_text).expanduser()
    try:
        policy = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise InvocationError("cannot read policy file {}: {}".format(path, error))
    if not policy:
        raise InvocationError("policy file is empty: {}".format(path))
    return policy


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
        help="emit human-readable text instead of stream-json",
    )
    parser.add_argument(
        "--policy-file",
        help="inject an engineering policy file into the request",
    )
    parser.add_argument("prompt", nargs="*", help="prompt text; reads stdin when omitted")
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        prompt = stdin.read().strip()
    if not prompt:
        raise InvocationError("prompt must be provided as arguments or stdin")

    return Request(
        cwd=Path(args.cwd).expanduser(),
        session_id=args.session_id,
        text_output=args.text,
        policy_text=_read_policy(args.policy_file),
        prompt=prompt,
    )


def _claude_command(
    prompt: str,
    model: str,
    session_id: Optional[str],
    text_output: bool,
    policy_text: Optional[str],
) -> List[str]:
    command = ["claude", "--safe-mode", "-p", prompt]
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
    if policy_text:
        command.extend(["--append-system-prompt", policy_text])
    return command


def _prompt_with_policy(prompt: str, policy_text: Optional[str]) -> str:
    if policy_text:
        return "{}\n\n# Task\n{}".format(policy_text, prompt)
    return prompt


def _codex_command(
    prompt: str,
    session_id: Optional[str],
    text_output: bool,
    policy_text: Optional[str],
) -> List[str]:
    command = ["codex", "exec"]
    if session_id:
        command.append("resume")
    command.extend(
        [
            "--model",
            "gpt-5.6-sol",
            "--config",
            'model_reasoning_effort="max"',
        ]
    )
    if not text_output:
        command.append("--json")
    if session_id:
        command.append(session_id)
    command.append(_prompt_with_policy(prompt, policy_text))
    return command


def _read_zai_key(environ: Mapping[str, str]) -> str:
    secret_root = environ.get("PERSONAL_SECRET_PATH")
    if not secret_root:
        raise InvocationError("PERSONAL_SECRET_PATH is not set")

    key_path = Path(secret_root).expanduser() / "zai.key"
    try:
        key = key_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise InvocationError("cannot read Z.AI key file {}: {}".format(key_path, error))

    parts = key.split(".")
    if len(parts) != 2 or not all(parts) or any(char.isspace() for char in key):
        raise InvocationError("Z.AI key has invalid format; expected {key-id}.{secret}")
    return key


def _glm_environment(environ: Mapping[str, str]) -> Dict[str, str]:
    child_environment = dict(environ)
    key = _read_zai_key(environ)

    for variable in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_VERTEX",
    ):
        child_environment.pop(variable, None)

    child_environment.update(
        {
            "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_BASE_URL": ZAI_BASE_URL,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.5-air",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": GLM_MODEL,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": GLM_MODEL,
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    return child_environment


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
    parent_environment = os.environ if environ is None else environ
    child_environment = dict(parent_environment)

    if backend == "kimi":
        child_environment["KIMI_MODEL_THINKING_EFFORT"] = "max"
        command = ["kimi"]
        if session_id:
            command.extend(["-S", session_id])
        command.extend(
            [
                "-m",
                "kimi-code/k3",
                "-p",
                _prompt_with_policy(prompt, policy_text),
                "--output-format",
                "text" if text_output else "stream-json",
            ]
        )
    elif backend == "claude":
        command = _claude_command(
            prompt=prompt,
            model="claude-opus-5[1m]",
            session_id=session_id,
            text_output=text_output,
            policy_text=policy_text,
        )
    elif backend == "codex":
        command = _codex_command(
            prompt=prompt,
            session_id=session_id,
            text_output=text_output,
            policy_text=policy_text,
        )
    elif backend == "glm":
        child_environment = _glm_environment(parent_environment)
        command = _claude_command(
            prompt=prompt,
            model="opus",
            session_id=session_id,
            text_output=text_output,
            policy_text=policy_text,
        )
    else:
        raise InvocationError("unknown backend: {}".format(backend))

    return Invocation(command=command, environment=child_environment)


def main(backend: str, argv: Optional[Sequence[str]] = None) -> int:
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
        )
        executable = shutil.which(
            invocation.command[0], path=invocation.environment.get("PATH")
        )
        if executable is None:
            raise InvocationError("executable is not available: {}".format(invocation.command[0]))

        os.chdir(str(request.cwd))
        os.execvpe(executable, invocation.command, invocation.environment)
    except InvocationError as error:
        print("ask-{}: error: {}".format(backend, error), file=sys.stderr)
        return 2
    except OSError as error:
        print("ask-{}: error: {}".format(backend, error), file=sys.stderr)
        return 126
    return 0

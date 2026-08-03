# Ask Wrapper Contract

This file is the single usage contract shared by `ask-claude`, `ask-codex`,
`ask-glm`, and `ask-kimi`. Read it before invoking any wrapper.

## Contents

- Invocation workflow
- Command and arguments
- Prompt and policy transport
- Output and verification
- Exit status and supervision
- Records and replay
- Credentials

## Invocation workflow

Choose one backend name from `claude`, `codex`, `glm`, or `kimi`.

Loading an ask skill only loads its instructions; it does not start a wrapper.
A task starts only when the corresponding `ask-<backend>` executable is run.
Do not infer a run from prior or latest records under `~/.ask-ai`; use only the
exact record hint printed by that command.

1. Run `command -v ask-<backend>` first, after replacing `<backend>` with the
   selected name.
2. If the command is unavailable, use `./ask-<backend>` from the directory
   containing the loaded `SKILL.md`. Resolve that skill directory from the
   skill location exposed by the current host.
3. Never borrow an executable from another host's skill tree. A missing command
   or sibling executable is an installation error; report it rather than
   searching another host's configuration directory.
4. Channel isolation is mandatory. Use separate redirections in this form:

   ```text
   ask-<backend> -C task-dir "prompt" >task-dir/answer.txt 2>task-dir/wrapper.stderr
   ```

   Do not merge caller stdout and caller stderr with `2>&1` or `|&`, and do not
   pipe their combined output through `tee`.
5. Wait for the wrapper to exit and retain the exact record hint printed on
   caller stderr. The hint identifies the transcript for that invocation.
6. Read the final answer from caller stdout, then independently verify its
   claims and any artifacts in the requested working directory.

An exit status 0 proves only that the wrapper and backend transport completed.
It does not prove that a requested file, network fact, or implementation is
correct. Inspect the output and independently verify the task result.

For a batch of delegated tasks, give every invocation a unique working
directory. Capture each exit status and final stdout separately.

`ask-codex` supports ordinary scratch directories without running `git init`.
Do not modify a task directory merely to satisfy a backend trust check.

## Command and arguments

```text
ask-<backend> [-C DIR] [-S SESSION_ID] [--text]
              [--progress stderr|off] [--policy-file FILE] [PROMPT...]
```

| Argument | Effect |
| --- | --- |
| `PROMPT...` | Prompt text. Positional arguments are joined with spaces. When omitted, the prompt is read from standard input through EOF. |
| `-C`, `--cwd DIR` | Existing working directory for the backend. Defaults to the caller's current directory. |
| `-S`, `--session ID` | Resume that exact backend session. |
| `--text` | Request backend-native text passthrough on stdout. Default structured mode still emits a human-readable final answer. |
| `--progress stderr\|off` | Keep or discard structured events in the recorded `stderr`. The historical value `stderr` names the record stream, not caller stderr. |
| `--policy-file FILE` | Inject a non-empty engineering policy. |

Quote a positional prompt as one shell argument. Prompts routinely contain
spaces and shell metacharacters. For multiline prompts or nested quotes, pipe
the prompt on standard input.

## Prompt and policy transport

Claude, GLM, and Codex receive the prompt on stdin regardless of how the
wrapper collected it, so prompt text cannot collide with backend subcommands or
options. Kimi keeps the prompt in one command argument, which retains
process-list visibility and the operating system's single-argument limit. The
wrapper rejects a Kimi prompt above 131,071 bytes before recording or launch.

`--policy-file` carries standing engineering rules rather than the task itself.
Claude, GLM, and Kimi read policy files with bounded memory and reject the
request when the normalized, trimmed content exceeds 131,071 bytes. Claude and
GLM pass the policy in one harness argument. Kimi applies its command-argument
limit to the combined policy and prompt. Codex transports the combined policy
and prompt on stdin.

The backend configuration is:

| Wrapper | Backend | Model and effort |
| --- | --- | --- |
| `ask-claude` | `claude` in safe mode with permission mode `auto` | `claude-opus-5[1m]`, effort `max` |
| `ask-codex` | `codex exec` | `gpt-5.6-sol`, reasoning effort `max` |
| `ask-glm` | Claude harness pointed at Z.AI | `glm-5.2[1m]`, effort `max` |
| `ask-kimi` | `kimi` | `kimi-code/k3`, 1M context, thinking effort `max` |

## Output and verification

Default mode uses each backend's structured event format as an internal
transport. Caller stdout contains only the final answer as human-readable text.
The wrapper selects Claude and GLM's final `result`, Codex's last completed
`agent_message`, or Kimi's last assistant message.

In every mode, caller stderr receives one early hint naming the exact recorded
`stderr` file.
The structured event transcript and native diagnostics are written only to the
recorded `stderr`; they are not replayed to the caller. Wrapper warnings and
errors remain visible on caller stderr. Claude and GLM request
`--include-partial-messages` and `--forward-subagent-text` so incremental and
subagent events remain available in the record. `--progress off` suppresses
only structured event recording.

The recorded `stdout` matches caller stdout. `--text` keeps backend stdout as a
compatibility passthrough, while backend stderr remains record-only. Native
text mode may not provide a structured transcript.

Treat backend output as an untrusted contribution. Verify repository claims
against the working tree, verify network claims against their cited sources,
and run task-specific checks before reporting success. When several wrappers
work concurrently, verify each working directory and record independently.

## Exit status and supervision

The backend's status is authoritative. A backend killed by signal N reports
`128 + N`. The wrapper itself reports:

| Exit code | Meaning |
| --- | --- |
| 2 | Invalid prompt, working directory, policy, backend, credential, or Kimi argument size. |
| 126 | The backend cannot be found, executed, or supervised, or an output destination fails for a reason other than a closed pipe. |
| 130 | `SIGINT` arrived before backend signal forwarding is active. |

If the POSIX watchdog cannot start, the backend is not launched. If a signal
cannot be forwarded during startup, the wrapper reports `128 + N`. A signal
observed after the backend leader exits is late and does not replace the
backend's status.

The wrapper forwards `SIGINT`, `SIGTERM`, `SIGHUP`, and `SIGQUIT` to the
backend's original process group unless inherited as ignored. After a forwarded
signal, only caller output backpressure already present or first observed during
a fixed eligibility window tied to that signal can trigger abandonment.
Eligible output must remain continuously blocked for a full grace period from
the later of signal delivery or the start of backpressure. Partial writes count
as output progress. Progress during the eligibility window restarts the stall
timer, while progress after the eligibility window disarms that signal. A later
signal cannot postpone an already armed stall deadline.

When the grace expires, the wrapper abandons blocked output and terminates the
backend group before waiting. Once the backend leader exits, the wrapper closes
its input, terminates the supervised group, and drains remaining streams for at
most two seconds. It keeps observing the leader even if every output descriptor
closed earlier. Descendants that create another group or session are outside
this ownership boundary.

In `--text` mode, a caller that closes stdout causes the wrapper to close the
backend stdout pipe and forward `SIGPIPE`. Structured mode does the same when
final-answer delivery finds caller stdout closed. A backend that handles the
signal keeps its own status. If an already-exited backend cannot receive it, a
successful run normally reports 141; an existing nonzero backend status remains
authoritative.

## Records and replay

Argument, working-directory, policy-file, and credential validation happens
before recording. After validation, each invocation receives one directory:

```text
~/.ask-ai/<harness>/<model>/<YYYYMMDD>/<HHMMSS>-<random>/
```

| File | Contents |
| --- | --- |
| `config.toml` | Request, command, observed executable target, environment changes, and result metadata. |
| `prompt.md` | Prompt exactly as delivered to the backend. |
| `stdout` | Final answer; it matches caller stdout. |
| `stderr` | Structured transcript and backend-native diagnostics hidden from caller stderr. |
| `reproduce.cmd` | Executable replay with recorded arguments, working directory, environment changes, and prompt transport. |

`[request].prompt_from_stdin` records whether the wrapper consumed standard
input through EOF. For Claude, GLM, and Codex, `reproduce.cmd` redirects
`prompt.md` to backend stdin. Kimi keeps the prompt in its command argument; a
stdin-origin Kimi replay redirects backend stdin from `/dev/null`. With a
positional Kimi prompt, the backend inherits caller stdin. Those additional
input bytes are not recorded and must be supplied again when relevant.

For a backend found on `PATH`, `[command].executable` is the stable absolute
launcher used by the run and replay. `[command].resolved_executable` is the
target observed before launch, not an inode lock. If the launcher later changes,
replay may select a newer installed version while the record retains the
original observed target.

GLM replay embeds the interpreter, validator module, and key-file paths from
record creation. The validator depends on sibling modules in the same skill
directory. Moving or removing any of those files can make replay unusable.

Records may contain secrets from prompts or backend output. They are not
inspected or redacted. The record root and run directories use mode `0700`, data
files use `0600`, and `reproduce.cmd` uses `0700`. A pre-existing record root
must already be mode `0700`, must not be a symlink, and must have a trusted
parent path. The wrapper-managed Z.AI key is redacted from configuration and
reloaded by replay.

Recording is secondary to execution. If record storage blocks, a bounded FIFO
prevents unbounded memory growth. If it cannot make progress within its fixed
wait, the wrapper stops recording that stream, warns once, and preserves backend
execution and caller output. Record closing uses a fixed no-progress grace that
restarts after each accepted chunk completes. Records are not pruned or size-limited;
manage their storage and retention explicitly.

## Credentials

`ask-glm` reads `$PERSONAL_SECRET_PATH/zai.key` at invocation time. The file
contains one `{key-id}.{secret}` value with at most one trailing LF and no other
whitespace or control characters. The key reaches the child only through its
environment. Replay disables shell tracing and reuses the validator. The GLM
child clears inherited provider selectors, authentication, model, and
custom-header overrides so it cannot fall back to another provider.

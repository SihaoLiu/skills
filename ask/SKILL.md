---
name: ask
description: >-
  Non-interactive interface to the other coding backends. Runs one prompt
  against a chosen backend through the ask-claude, ask-codex, ask-glm, and
  ask-kimi wrappers and separates the final response from its execution transcript. Use when
  delegating a self-contained build or investigation task to another backend,
  collecting an independent review of a design, plan, or diff, asking the same
  question of several backends to compare answers, or resuming an earlier
  backend session by ID. Triggers on "ask codex", "ask kimi", "ask glm",
  "ask claude", "get a second opinion from another model", or any request to
  hand work to a different coding backend without an interactive session.
argument-hint: '<backend> [-C DIR] [-S SESSION_ID] [--text] [--progress stderr|off] [--policy-file FILE] <prompt>'
user-invocable: true
---

# Ask

One interface for running a prompt against another coding backend without an
interactive session. Each backend has its own wrapper, and all of them share the
same arguments, so switching backends means changing only the program name.

The wrappers live beside this file. Invoke them by absolute path; they are not
on `PATH`.

```text
<skill directory>/ask-<backend> [-C DIR] [-S SESSION_ID] [--text]
                                [--progress stderr|off] [--policy-file FILE]
                                [PROMPT...]
```

Quote the prompt as a single argument. It routinely contains spaces and shell
metacharacters such as `(`, `)`, `;`, `#`, `*`, and `[`, and an unquoted prompt
is re-parsed by the shell before the wrapper ever starts. When the prompt is
multiline or itself contains quotes, pipe it in on standard input instead.
Claude, GLM, and Codex receive the prompt on stdin regardless of how the wrapper
collected it, so prompt text cannot collide with backend subcommands or options.
Kimi keeps the prompt in one command argument, which retains process-list
visibility and the operating system's single-argument limit. The wrapper
rejects a Kimi prompt above 131,071 bytes before recording or launch.

## Arguments

| Argument | Effect |
| --- | --- |
| `PROMPT...` | Prompt text. All positional arguments are joined with spaces. When none are given, the prompt is read from standard input. |
| `-C`, `--cwd DIR` | Working directory for the backend. Defaults to the current directory and must already exist. |
| `-S`, `--session ID` | Resume that exact backend session instead of starting a new one. |
| `--text` | Compatibility mode that requests backend-native text on stdout. Default mode already emits a human-readable final answer and also provides a structured transcript. |
| `--progress stderr\|off` | Record the backend's structured event transcript in the run's `stderr` file. This defaults to `stderr`; `off` discards those events while retaining the final answer and native diagnostics. |
| `--policy-file FILE` | Prepend an engineering policy to the request. The file must exist and be non-empty. |

## Backends

| Wrapper | Backend invoked | Model and effort |
| --- | --- | --- |
| `ask-kimi` | `kimi` | `kimi-code/k3`, 1M context, thinking effort `max` |
| `ask-claude` | `claude` in safe mode, permission mode `auto` | pinned `claude-opus-5[1m]`, effort `max` |
| `ask-codex` | `codex exec` | `gpt-5.6-sol`, reasoning effort `max` |
| `ask-glm` | the Claude harness pointed at Z.AI | `glm-5.2[1m]`, effort `max` |

## Policy files

`--policy-file` carries standing engineering rules that are not part of the task
itself. It is delivered as a system-level instruction where the backend supports
one, and otherwise prepended to the prompt above a `# Task` heading. Use it to
state invariants the backend must respect; keep the prompt itself about the work
to be done. Claude, GLM, and Kimi read policy files with bounded memory and
reject the request when the normalized, trimmed content exceeds 131,071 bytes.
Claude and GLM then pass the policy in one harness argument. Kimi applies its
existing command-argument limit to the combined policy and prompt. Codex
transports the combined policy and prompt on stdin and has no command-argument
policy limit.

## Output and exit status

By default, the wrapper requests each backend's structured event format as an
internal transport. stdout contains only the final answer as human-readable
text. The wrapper selects Claude and GLM's final `result`, Codex's last completed
`agent_message`, or Kimi's last assistant message, then writes it when the
backend closes its event stream.

In every mode, caller stderr receives one early hint naming the exact recorded
`stderr` file.
The backend's structured event transcript and native diagnostics are written
only to the recorded `stderr`; they are not replayed to the caller. This
preserves reasoning summaries, counters, tool calls, tool results, subagent
messages, and other process details without adding them to the caller's
context. Claude and GLM request `--include-partial-messages` and
`--forward-subagent-text` so incremental and subagent events are included.
`--progress off` suppresses only the structured event transcript. Wrapper
warnings and errors remain visible on caller stderr.

`--text` keeps backend stdout as a compatibility passthrough; backend stderr
remains record-only, and native text mode may not provide a process transcript.
The default mode should be preferred for both a readable result and a
replayable transcript.

The exit status is the backend's own, and a backend killed by signal N reports
as `128 + N`. Three cases the wrapper reports itself:

| Exit code | Meaning |
| --- | --- |
| 2 | Invalid request: empty prompt, missing working directory, unreadable, empty, or oversized policy file, unknown backend, a malformed Z.AI key, or a Kimi prompt above the command-argument limit. |
| 126 | The backend program is not on `PATH`, could not be executed or safely supervised, or an output destination fails for a reason other than a closed pipe. |
| 130 | The wrapper receives `SIGINT` before backend signal forwarding is active. |

If the POSIX watchdog cannot start, the backend is not launched. This is a
runtime supervision failure, so a record created after request validation may
still contain the exit status and diagnostic.

If a signal cannot be forwarded while the backend is starting, the wrapper
reports `128 + N` for that signal. A signal observed after the backend leader
has already exited is late and does not replace the backend's status.

In `--text` mode, if the caller closes stdout, the wrapper closes the backend
stdout pipe and forwards `SIGPIPE`. Default mode discovers a closed caller
stdout when it delivers the final answer and likewise forwards `SIGPIPE`. A
backend that handles `SIGPIPE` keeps its own exit status. If the backend has
already exited and cannot receive the signal, an otherwise successful run
reports `128 + SIGPIPE` (normally 141); an existing nonzero backend status
remains authoritative.

The backend writes to pipes rather than to a terminal, so it drops colour and
terminal progress rendering. Default mode uses structured events instead. A
backend that only line-buffers on a terminal may deliver `--text` output in
larger blocks.

The wrapper's startup hint and any warnings go to caller stderr and are never
mixed into the recorded backend streams. A normal startup hint has this form:

```text
Hint: please check <record-directory>/stderr for in-progress output.
```

The wrapper forwards `SIGINT`, `SIGTERM`, `SIGHUP`, and `SIGQUIT` to the
backend's original process group, except for dispositions inherited as
ignored. After a forwarded signal, only caller output backpressure already
present or first observed during a fixed eligibility window tied to that signal
can trigger abandonment. Eligible output must remain continuously blocked for a
full grace period from the later of signal delivery or the start of
backpressure. Partial writes count as output progress. Progress during the
eligibility window restarts the continuous stall timer, while progress after the
eligibility window disarms that signal because its output was not continuously
blocked. Output that resumes, or first blocks after the eligibility window,
cannot be abandoned by that signal. A later signal cannot postpone an already
armed stall deadline; later signals otherwise have their own eligibility. When
the continuous grace expires, the wrapper abandons that output and terminates
the backend group before waiting. Once the backend leader exits, the wrapper
closes its input, terminates its supervised group, and drains remaining stream
data for at most two seconds. It keeps observing the leader even if every output
descriptor closed earlier, including on POSIX systems where observing the leader
also reaps it. A descendant that creates a new process group or session is
outside this ownership boundary; its later output is cut off when the drain
deadline expires. Other job-control signals are not relayed.

Read the output before acting on it. A backend that answers confidently can
still be wrong about this repository, so verify claims against the working tree
rather than relaying them.

## Records

Argument, working-directory, policy-file, and credential validation happens
before recording; those validation failures emit a diagnostic without creating
a record. After validation, each invocation is recorded under `~/.ask-ai`, one
directory per run:

```text
~/.ask-ai/<harness>/<model>/<YYYYMMDD>/<HHMMSS>-<random>/
```

`<harness>` is the selected backend program, so `ask-glm` records under
`claude/glm-5.2-1m` and `ask-claude` under `claude/claude-opus-5-1m`. The
wrapper prints a caller-stderr hint naming that run's `stderr` file as the run
starts.

| File | Contents |
| --- | --- |
| `config.toml` | The request, the command and its observed resolved target, the environment the wrapper changed, and a `[result]` table appended when the run ends. |
| `prompt.md` | The prompt exactly as delivered to the backend. `ask-codex` and `ask-kimi` carry the policy inside it, `ask-claude` and `ask-glm` pass the policy separately, and `policy_inlined` records which happened. |
| `stdout`, `stderr` | The final answer and process transcript. In every mode, recorded `stdout` matches caller stdout. Recorded `stderr` retains the structured transcript and backend-native diagnostics that are hidden from the caller. |
| `reproduce.cmd` | An executable `/bin/sh` script that re-runs the same invocation: the observed program path, every argument expanded and quoted, the working directory, and the environment changes. |

`[request].prompt_from_stdin` says whether the wrapper consumed standard input
through EOF to obtain the prompt. For Claude, GLM, and Codex, `reproduce.cmd`
redirects `prompt.md` to backend stdin, matching the live transport. Kimi keeps
the prompt in its command argument; when the wrapper originally consumed stdin,
its replay redirects backend stdin from `/dev/null` to preserve EOF. With a
positional Kimi prompt, the backend inherits the caller's stdin. Those additional
input bytes are not recorded, so a replay that depends on them must provide them
again.

For a backend found on `PATH`, `[command].executable` is the stable absolute
launcher used by both the original run and `reproduce.cmd`.
`[command].resolved_executable` is the target observed through that launcher
before the run; it is forensic context, not an inode lock. If the launcher
changes later, replay may select a newer installed version while the record
still shows which target was observed originally. When lookup fails, the
record retains the bare harness name, omits `resolved_executable`, and a later
replay performs a fresh `PATH` lookup.

GLM replay embeds the absolute interpreter, validator module, and key-file paths
observed when the record is created. The validator also depends on its sibling
modules in the same skill directory. Moving or removing any of those files can
make that replay unusable even when the backend launcher remains available.

Backends report their session ID in structured events, so the recorded stderr
transcript is also where to recover an ID for a later `-S` resume.

A record holds the delivered prompt, displayed final answer, and process
transcript. Those files are not inspected or redacted and may contain secrets
supplied by the prompt or printed by the backend. The record root and each run
directory are mode `0700`;
its data files are mode `0600`, and `reproduce.cmd` is mode `0700`. A
pre-existing record root must already have mode `0700`; the wrapper will not
change the permissions of an arbitrary `ASK_AI_HOME` or follow one that is a
symbolic link. The parent path must be trusted while the wrapper initially
creates or opens the record root. Once open, directory and file creation stays
anchored to verified directory descriptors, so later path replacement cannot
redirect recorded data. The wrapper-managed Z.AI key is shown as `<redacted>`
in `config.toml`, and `reproduce.cmd` re-reads it from its key file instead of
embedding it.

Set `ASK_AI_HOME` to record somewhere else. Recording is secondary to running:
when a record artifact cannot be written the wrapper warns on stderr and runs
the backend anyway, so the affected record may be incomplete. Normal record
writes enter a bounded FIFO in order, so an in-flight write does not suppress
later chunks or permit unbounded memory growth. If the FIFO cannot make progress
within its fixed wait, or record closing sees no progress for a fixed no-progress
grace, the wrapper stops recording that stream, warns once, and continues the
backend execution and caller output defined by the selected mode. The closing
deadline restarts after each accepted chunk completes, preserving a healthy
queue tail; the bounded FIFO keeps the total shutdown wait finite. In other
words, if record storage blocks, backend execution and caller output remain
authoritative.
Records are not pruned or size-limited; choose a location with enough capacity
and manage its retention explicitly.

## Credentials

`ask-glm` reads the Z.AI key from `$PERSONAL_SECRET_PATH/zai.key` at invocation
time. The file must contain one key in `{key-id}.{secret}` form, with at most
one trailing LF and no other whitespace or control characters. The key
reaches the child process through its environment, never through command
arguments. The wrapper does not serialize it into persistent configuration or
the replay script. Replay disables shell tracing before loading the key and
uses the same validator as the original invocation. A backend can still print
environment values, and its displayed output is recorded as described above. The
wrapper also clears inherited provider selectors, authentication, model, and
custom-header overrides from that child environment so the session cannot fall
back to the wrong provider.

## Scope

These wrappers only launch a backend. Choosing the policy, validating the
output, testing the result independently, feeding corrections back through the
same session, and deciding whether the work can be integrated all remain with
the caller.

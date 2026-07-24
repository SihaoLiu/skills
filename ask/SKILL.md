---
name: ask
description: >-
  Non-interactive interface to the other coding backends. Runs one prompt
  against a chosen backend through the ask-claude, ask-codex, ask-glm, and
  ask-kimi wrappers and returns its output as stream-json or text. Use when
  delegating a self-contained build or investigation task to another backend,
  collecting an independent review of a design, plan, or diff, asking the same
  question of several backends to compare answers, or resuming an earlier
  backend session by ID. Triggers on "ask codex", "ask kimi", "ask glm",
  "ask claude", "get a second opinion from another model", or any request to
  hand work to a different coding backend without an interactive session.
argument-hint: '<backend> [-C DIR] [-S SESSION_ID] [--text] [--policy-file FILE] <prompt>'
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
                                [--policy-file FILE] [PROMPT...]
```

Quote the prompt as a single argument. It routinely contains spaces and shell
metacharacters such as `(`, `)`, `;`, `#`, `*`, and `[`, and an unquoted prompt
is re-parsed by the shell before the wrapper ever starts. When the prompt is
long or itself contains quotes, pipe it in on standard input instead.

## Arguments

| Argument | Effect |
| --- | --- |
| `PROMPT...` | Prompt text. All positional arguments are joined with spaces. When none are given, the prompt is read from standard input. |
| `-C`, `--cwd DIR` | Working directory for the backend. Defaults to the current directory and must already exist. |
| `-S`, `--session ID` | Resume that exact backend session instead of starting a new one. |
| `--text` | Emit human-readable text. Without it the output is `stream-json`. |
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
to be done.

## Output and exit status

The wrapper replaces itself with the backend process, so the exit status is the
backend's own, except for two cases it reports itself:

| Exit code | Meaning |
| --- | --- |
| 2 | Invalid request: empty prompt, missing working directory, unreadable or empty policy file, unknown backend, or a malformed Z.AI key. |
| 126 | The backend program is not installed or could not be executed. |

Read the output before acting on it. A backend that answers confidently can
still be wrong about this repository, so verify claims against the working tree
rather than relaying them.

## Credentials

`ask-glm` reads the Z.AI key from `$PERSONAL_SECRET_PATH/zai.key` at invocation
time. The file must contain only a key in `{key-id}.{secret}` form. The key
reaches the child process through its environment, never through command
arguments, and is never written to persistent configuration. The wrapper also
clears inherited Claude authentication variables from that child environment so
the session cannot fall back to the wrong provider.

## Scope

These wrappers only launch a backend. Choosing the policy, validating the
output, testing the result independently, feeding corrections back through the
same session, and deciding whether the work can be integrated all remain with
the caller.

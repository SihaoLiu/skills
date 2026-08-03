---
name: ask-kimi
description: >-
  Use when delegating a self-contained build or investigation to Kimi,
  requesting an independent Kimi review, or resuming an exact Kimi session.
argument-hint: '[-C DIR] [-S SESSION_ID] [--text] [--progress stderr|off] [--policy-file FILE] <prompt>'
user-invocable: true
---

# Ask Kimi

Before invoking this wrapper, read [the shared ask contract](../_ask_lib/USAGE.md)
completely and follow its discovery, execution, output, verification, and record
rules.

Use `ask-kimi` for this backend. Its executable is also beside this file.

## Backend

The wrapper runs `kimi` with `kimi-code/k3`, 1M context, and thinking effort
`max`. Kimi receives the combined prompt in one command argument, so the shared
size and process-visibility limits apply.

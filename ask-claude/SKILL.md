---
name: ask-claude
description: >-
  Use when delegating a self-contained build or investigation to Claude,
  requesting an independent Claude review, or resuming an exact Claude session.
argument-hint: '[-C DIR] [-S SESSION_ID] [--text] [--progress stderr|off] [--policy-file FILE] <prompt>'
user-invocable: true
---

# Ask Claude

Before invoking this wrapper, read [the shared ask contract](../_ask_lib/USAGE.md)
completely and follow its discovery, execution, output, verification, and record
rules.

Use `ask-claude` for this backend. Its executable is also beside this file.

## Backend

The wrapper runs `claude` in safe mode with permission mode `auto`, pins
`claude-opus-5[1m]`, and requests effort `max`. Structured mode requests partial
messages and forwarded subagent text for the recorded transcript.

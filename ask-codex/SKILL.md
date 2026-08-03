---
name: ask-codex
description: >-
  Use when delegating a self-contained build or investigation to Codex,
  requesting an independent Codex review, or resuming an exact Codex session.
argument-hint: '[-C DIR] [-S SESSION_ID] [--text] [--progress stderr|off] [--policy-file FILE] <prompt>'
user-invocable: true
---

# Ask Codex

Before invoking this wrapper, read [the shared ask contract](../_ask_lib/USAGE.md)
completely and follow its discovery, execution, output, verification, and record
rules.

Use `ask-codex` for this backend. Its executable is also beside this file.

## Backend

The wrapper runs `codex exec`, pins `gpt-5.6-sol`, and requests reasoning effort
`max`. It accepts scratch working directories without requiring a Git repository.

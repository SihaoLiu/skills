---
name: ask-glm
description: >-
  Use when delegating a self-contained build or investigation to GLM through
  Z.AI, requesting an independent GLM review, or resuming an exact GLM session.
argument-hint: '[-C DIR] [-S SESSION_ID] [--text] [--progress stderr|off] [--policy-file FILE] <prompt>'
user-invocable: true
---

# Ask GLM

Before invoking this wrapper, read [the shared ask contract](../_ask_lib/USAGE.md)
completely and follow its discovery, execution, output, verification, and record
rules.

Use `ask-glm` for this backend. Its executable is also beside this file.

## Backend

The wrapper runs the Claude harness against Z.AI, pins `glm-5.2[1m]`, and
requests effort `max`. It reads the Z.AI credential from
`$PERSONAL_SECRET_PATH/zai.key` at invocation time.

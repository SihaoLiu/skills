---
name: monitor-codex-goal
description: >-
  Read-only third-party overseer for a separate Codex CLI session that is
  autonomously running /goal. A dedicated Claude Code session audits the
  target's progress on a recurring self-managed cron tick using read-only
  Explore subagents, derives the project's drift criteria from the target's
  own transcript, and -- only through a hardened tmux injection script behind
  a human-authorization gate -- steers the running Codex. Use when you want to
  supervise an autonomous Codex /goal run for direction drift, fabricated data,
  fake/stub implementations, and broken project invariants, with phone alerts.
  Triggers on "monitor codex goal", "oversee codex /goal", "watch the codex
  session", "babysit the codex goal run", or any request to supervise or steer
  an autonomous Codex build loop from a separate session.
argument-hint: '<codex-session-id> <session:window.pane> [--discover] [--cadence 1h] [--notify-only] [--steer "<raw request>"]'
user-invocable: true
---

# Monitor Codex Goal

A dedicated Claude Code session pairs with a *separate* Codex CLI session that is
autonomously running `/goal` (a long-running, self-driving build loop). It never
writes the target's code. It has two complementary roles, and **both ultimately
produce one thing: a steer prompt that fits the Codex `/goal` contract, delivered
into the running Codex through a hardened tmux script.**

> The session is an **auditor and a remote steering arm, not an automation
> agent.** Explicit locators, runtime-derived criteria, and a human-authorization
> gate are what make it reusable without making it dangerous.

## Two modes

- **Overseer mode (monitor-driven).** On a recurring, self-managed cron tick, the
  session audits the target read-only; when it finds a problem it drafts a steer,
  gets it authorized, and injects it. This is the bulk of this skill.
- **Relay mode (user-initiated).** You -- often from your phone over Remote
  Control -- hand the session a raw request in your own words. It rewrites that
  into a well-formed steer that respects the current goal's contract, shows you
  the rewrite to confirm, and injects it once. A one-shot prompt-rewrite-and-send
  with no monitoring loop. See **Relay mode**.

Both modes compose the steer the same way (see **Composing a steer prompt**) and
deliver it the same way (`scripts/inject-steer.sh`). The skill applies to *any*
Codex `/goal` session: it needs only where to read (the transcript) and where to
type (the tmux pane), and it bootstraps everything else -- including what counts
as "drift" -- from the target itself.

## Required inputs

```
/monitor-codex-goal <codex-session-id> <session:window.pane>
   [--discover] [--cadence 1h] [--principles "<extra rules>"] [--notify-only]
   [--steer "<raw request in your own words>"]
```

- `<codex-session-id>` -- the Codex session UUID. Resolves the transcript at
  `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`. The transcript is the
  read surface (progress + the human's prior steering).
- `<session:window.pane>` -- the tmux pane address where the Codex TUI is
  running, e.g. `Z-manage:main.2`. Codex usually occupies a single pane inside a
  window, not the whole window, so the locator is pane-precise. This is the
  inject surface.
- `--discover` -- run `scripts/locate-codex.sh` to sweep all tmux panes across
  all sessions, flag the ones that look like a live Codex TUI, and list recent
  Codex `/goal` transcripts, so the human can confirm the exact
  `(session-id, pane)` pairing before anything runs.
- `--cadence` -- tick interval (default `1h`).
- `--principles "..."` -- extra drift rules to merge on top of what is derived
  from the transcript (for rules the human holds that are not in the transcript).
- `--notify-only` -- kill-switch. Disables all auto-inject; every steer then
  requires explicit per-message approval.
- `--steer "..."` -- **Relay mode.** Skip the monitoring loop entirely: take this
  raw request, rewrite it into a goal-fitting steer, confirm with the human, and
  inject it once. See **Relay mode**.

The session must run in its own pane on the **same tmux server** as the target,
and must have **Remote Control connected** (see Remote channel requirement) --
which is also how you reach Relay mode from your phone.

## Read-only safety contract

This is load-bearing, not advisory. The overseer's value depends on it being
unable to corrupt what it watches.

**Allowed (read-only):** `git status/diff/log/show`, `rg`/`grep`, `jq`, `tail`,
`head`, `sed` (read), `ls`, `find`, `stat`, `wc`, `python` for parsing only,
`tmux capture-pane` / `list-panes` / `display-message`.

**Banned:** any write into the target repo's tracked files; `apply_patch` or any
editor; `git add/commit/reset/checkout/clean`; builds or tests that mutate or
produce artifacts; package installs; deletions; **modifying the target
transcript**; and raw `tmux send-keys` -- the *only* sanctioned keystrokes to
Codex go through `scripts/inject-steer.sh` under the gate below.

**Subagents are Explore-type only** -- they physically lack Edit/Write/build
tools. Their prompts must also state, in words, that they may only read.

**The only writes the skill itself performs:** (a) its own cron via
`CronCreate`/`CronDelete`, (b) `PushNotification`, and (c) ephemeral injection
payloads + evidence dumps under a scratch `temp/` dir. None of these touch the
target repo or transcript, so the read-only guarantee over the things that
matter holds -- and the evidence dumps make every injection auditable.

## Remote channel requirement

Phone alerts are the backbone: if the overseer runs but its alerts reach no one,
the whole steering loop is silently broken. Claude Code exposes no direct
"am I remote-connected" query, but the `PushNotification` tool **result** is an
authoritative end-to-end signal -- when Remote Control is connected and a mobile
is registered with push enabled, the result reads like
`Terminal notification sent. Mobile push requested.`; otherwise it reports the
push was not sent.

Policy: **refuse entirely until the channel is verified live.**

- **Startup preflight (mandatory):** send one `"monitor armed for <id>"` push and
  parse the result. If mobile push is not confirmed, **do not arm** -- print the
  fix steps and stop:
  1. Connect Remote Control: run `/remote-control` (or start with
     `claude --remote-control`).
  2. Install the Claude mobile app and sign in with the same account.
  3. In the terminal, `/config` -> enable **Push when Claude decides**.
  4. Open the app once so it registers its push token, then re-arm.
- **Every tick opens with a liveness/heartbeat push** that both proves the
  channel and reports the tick is running. If that push is not delivered to the
  phone, **halt the tick** (no audit action, no injection) and wait for the next
  tick. The channel coming back restores normal operation automatically.
- **Auto-inject is hard-gated on a verified-live channel this tick** -- its
  safety rail is the post-hoc "I injected X" push, which is worthless if the
  phone cannot be reached.

Consequence of the strict choice: a clean tick still emits the heartbeat push
(an hourly "alive, channel OK" by default). Each heartbeat carries the tick
verdict so it is signal, not noise. If hourly ever feels heavy, the cadence and
heartbeat frequency are a one-line change.

Known channel-drop causes to expect mid-run (all force a halt until restored):
laptop sleep, a network outage longer than ~10 minutes (Remote Control times
out), and starting an `ultraplan` session (it disconnects Remote Control).

## The monitoring tick

Each cron firing re-enters this skill **fresh**. Subagents do the heavy
transcript reading and return only conclusions, so the overseer's own context
stays lean across a multi-hour run. The tick, in order:

1. **Liveness push** -- heartbeat that verifies the phone channel and announces
   the tick. Not delivered -> halt, wait for next tick.
2. **Re-locate the target** -- resolve the transcript from the session id;
   confirm the tmux pane via `inject-steer.sh verify-target`. If the transcript
   is missing/ambiguous/rotated, or the session ended, or `/goal` is complete or
   blocked, or the pane is gone/renamed/not a live Codex TUI -> notify with the
   diagnosis and, for terminal conditions, offer `CronDelete`.
3. **Refresh drift criteria** -- re-derive from the transcript (see below),
   newer steering overriding older, merged with `--principles`.
4. **Dispatch three read-only Explore subagents** in parallel (see Audit
   dimensions), **each on the Opus model** (dispatch with `model: opus`).
   Strongly restate read-only in each prompt.
5. **Synthesize** against the four judgment criteria.
6. **Decide:** clean -> the heartbeat already reported it; leave a one-line
   conclusion in the session, no alert. Finding -> Intervention gate.

## Drift-criteria derivation

The strength of this skill is that "what counts as drift" is not hardcoded -- it
is read from the target session's own human steering history each tick.

- Parse the transcript **line-by-line** (it can be tens of MB; never read it
  whole). Each line is JSON with `type` and `payload`; `payload.type` is one of
  `user_message`, `agent_message`, `reasoning`, `function_call`,
  `patch_apply_end`, `thread_goal_updated`. Timestamps are ISO UTC.
- Extract the human's `user_message` entries and distill the project's
  first-principles rules from them.
- **Newer steering overrides older.** Each derived rule should cite its
  originating turn/timestamp. When two human instructions genuinely conflict,
  prefer the most recent explicit one; if still ambiguous, **ask the human --
  do not steer Codex on a guess.**
- Merge any `--principles` the human passed at invocation.

These derived rules are intentionally **not persisted** -- re-deriving each tick
keeps them in sync as the human's priorities evolve and avoids a second source
of truth.

## Audit dimensions

Three read-only Explore subagents per tick, each blind to the others, **each
dispatched on the Opus model** (the audit judgement needs the strongest model;
do not downgrade these to a smaller tier):

- **Transcript progress** -- what Codex did since the last checkpoint, what it
  claims, whether it ran its own independent audits or self-certified, and the
  `/goal` state (active / blocked / complete, token + time burn).
- **Git commits + worktree diff** since the last checkpoint HEAD -- red flags:
  hardcoded magic numbers used as simulation/expected RESULTS baked into
  producer code or tests; pass/status with no real artifact behind it; disabled
  or skipped functional tests; stub returns dressed as real computation;
  whether the unpushed-batch-then-review discipline is being followed; and
  commit-message hygiene.
- **Artifact reasonableness** -- the invariants here are **derived from the
  transcript/repo, not hardcoded.** Typical shapes: distinct
  kernels/apps must produce distinct numbers; results must trace to a real
  report (not fabricated, not all-zero, missing-evidence not written as zero);
  a constrained model must not be more optimistic than its idealized baseline;
  first-principles monotonicity (e.g. longer input must cost more).

## Judgment criteria

1. **Drift** -- violates the human's established principles. But **deep-diving
   or optimizing a fundamental design decision is NOT drift** -- it is exactly
   the kind of foundational work the human wants; only a genuine departure from
   stated principles counts.
2. **Fabricated data** -- hardcoded numbers as results, pass-without-evidence,
   or an artifact that exists but whose data is not reasonable.
3. **Fake / stub implementation** -- plausible-looking returns claimed as real
   hardware-aware / functional behavior.
4. **Project-specific invariant breaks** -- the derived invariants above, most
   notably distinct things reporting identical numbers, where the target's own
   audit may have let it through.

## Intervention gate

When a finding needs a steer, classify it first.

**Auto-inject eligible** (may inject without prior approval, behind the rails):
- Reinforcing a risk **Codex itself already surfaced** in its transcript but is
  at risk of dropping.
- Reinforcing an **already-established** human principle (anti-fabrication,
  no fake/stub, SSOT, and the like).
- Flagging a **grave-integrity hazard**: data fabrication, or claiming an
  implementation exists when it does not.

**Everything else requires per-message human approval** -- anything that
introduces new direction, reverses a decision, changes strategy or priority,
changes scope, or is novel or uncertain.

**Rails on every auto-inject:** the channel must be verified-live this tick;
**at most one auto-inject per tick** plus a cooldown; it is built from this
tick's fresh inspection and submitted within the same tick (never a carried
draft); and a **post-hoc PushNotification** always tells the human what was
injected. `--notify-only` disables this whole path.

### Approval path
1. `PushNotification` with the drafted steer prompt and the draft timestamp.
2. Wait for explicit human approval.
3. **Staleness rule:** if approval arrives within 10 minutes of the draft,
   inject. If it is older, the draft may be stale -- re-inspect Codex's current
   state, redraft from the latest progress, re-notify, and re-time. Never inject
   a stale draft.
4. Inject: `verify-target` -> `inject-steer.sh type` (text lands in Codex's
   input box, unsent) -> human can eyeball it in the pane -> `inject-steer.sh
   submit`.

### Auto-inject path
`verify-target` -> `inject-steer.sh send` (atomic type + submit) -> post-hoc
push naming what was injected.

The steer text itself, however drafted, follows **Composing a steer prompt**.

## Composing a steer prompt

Every steer this skill sends -- whether the overseer drafted it from a finding or
it is a rewrite of your raw request -- is a message injected into an *active*
Codex `/goal` thread. It must speak the goal contract's language, not just say
"keep going" or dump a vague ask. **Before composing or rewriting any steer, read
the bundled cookbook `references/codex-goal-cookbook.md`** (the guide to Codex
Goals) and make the steer consistent with how a goal is defined and audited.

A good steer:
- Names the **outcome / end state** it wants, in terms the live goal can audit.
- Points at the **verification surface** -- the test, benchmark, report, artifact,
  or evidence that proves it -- never "trust me, it is done".
- Restates the **constraints** that must not regress (the project's established
  principles: SSOT, no fabricated data, no fake/stub, and so on).
- Respects the goal's **boundaries** (the files, tools, and scope already in play).
- Says how Codex should choose the **next action**, and when to treat itself as
  **blocked** rather than declare false success.
- Stays narrow enough to audit but open enough for Codex to investigate.

Keep it tight and single-purpose: a steer *augments* the live goal, it does not
restate or redefine the whole goal.

## Relay mode (user-initiated steer)

Relay mode is the skill acting as your remote steering arm: you supply intent, it
supplies the goal-fitting phrasing and the mechanical delivery. It is a one-shot
-- it does NOT create a cron or start monitoring.

Trigger it by invoking with `--steer "<raw request>"`, or, inside an
already-running overseer session, by simply messaging the request (e.g. from your
phone: "tell codex to also cover the q15 variants"). The flow:

1. **Verify channel and target** -- you are actively driving, so the live channel
   is implicit, but still `verify-target` the pane before injecting.
2. **Read just enough context** -- skim the transcript's recent state and the
   active goal so the steer fits what Codex is doing now. If the request
   contradicts the current goal or an established principle, surface that and ask,
   rather than silently injecting something incoherent.
3. **Rewrite** the raw request into a steer per **Composing a steer prompt** (with
   the cookbook). Preserve your intent; add the contract structure.
4. **Confirm** -- show you the rewritten steer verbatim and wait for your OK. This
   confirms the *phrasing* of your own request; it is not the overseer's tiered
   gate.
5. **Inject** -- `verify-target` -> `inject-steer.sh type` -> (you can eyeball it
   in the pane) -> `inject-steer.sh submit`; or `send` if you said "just send it".
   Report the exit code; on failure, surface the evidence dump.

A rewritten steer is always shown before it goes -- Relay mode never auto-fires.

## The injection script

`scripts/inject-steer.sh` makes the inject dance **deterministic** instead of
LLM-improvised. A TUI's render timing is racy; reacting to pane text by hand
risks fumbling quoting on CJK/quotes/newlines or double-submitting. The script
turns "looks submitted, probably" into a checkable state machine with exit codes.

Path: `~/.claude/skills/monitor-codex-goal/scripts/inject-steer.sh`

Subcommands (text is always passed via a **file**, never argv):

| Subcommand | Contract |
|---|---|
| `verify-target <pane>` | Non-mutating health check: pane resolves, not dead, input not off, not in copy mode, identified as a live Codex process, capture nonempty and stable. (Codex runs on the PRIMARY screen, so the target is matched by process tree, not alternate-screen -- which is what stops a steer from landing in a plain shell or the monitor's own Claude pane.) |
| `type <pane> <file>` | `load-buffer` then `paste-buffer -p -r` (bracketed paste; `-r` stops LF being turned into Enter), then verify the text landed near the input -- either as a normalized tail-signature (short pastes render inline) or as a collapsed `[Pasted Content N chars]` placeholder whose count matches the file (long pastes; Codex collapses them in the composer). No Enter. |
| `submit <pane> [file]` | Press Enter, verify submission by watching the steer leave the live input line (literal signature *or* the collapsed placeholder), retry Enter once **only** if there was no pane delta and the steer is still in the input region; stop on any ambiguity. At most two Enters. |
| `send <pane> <file>` | Atomic `type` + `submit` for the auto-inject path. |

Exit codes: `0` success, `10` target missing/dead, `11` not a live Codex TUI,
`20` pane busy/unstable, `30` paste failed, `31` text-landed verify failed,
`40` submit verify failed, `41` ambiguous post-Enter (retry suppressed),
`64` usage error. The orchestrating session reacts to the exit code -- it never
eyeballs pane text to decide success. A nonzero exit means **do not assume the
steer was sent**: surface the evidence dump and (auto-inject) treat as failed +
notify, or (approval path) hand it back to the human.

Every run writes evidence (target metadata, before/after-paste/after-enter
captures, byte count + hash) under a scratch `temp/steer-<ts>/` dir.

## Loop and cadence

This applies to **overseer mode only** -- a `--steer` (Relay mode) invocation is
one-shot and never creates a cron.

In overseer mode the skill **self-manages its own cron.** On first invocation:
run the channel preflight, then one tick immediately, then `CronCreate` a
recurring job (default hourly, on an off-the-hour minute to dodge top-of-hour
congestion). Report the
cron id so the human can stop it. The job re-enters this skill each firing.
`CronDelete` the job on termination, when `/goal` completes, or when the target
is gone.

The cron lives in the overseer session's memory: **closing the monitor session
or its tmux pane stops the loop.** The monitor pane must stay alive.

## Failure modes

- Transcript not found / multiple matches / rotated -> halt the tick, notify the
  human to re-specify; never guess a different transcript.
- Session ended, or `/goal` complete or blocked -> terminal-condition push,
  offer `CronDelete`.
- Pane missing / renamed / reused, or `verify-target` fails (not Codex, in copy
  mode, input off, dead) -> **never inject**, notify.
- Multiple `/goal` candidates -> `--discover` lists them, human confirms.
- Derived-criteria conflict -> newer human instruction wins; genuine ambiguity
  -> ask the human, do not steer.
- Phone channel down -> halt the tick (refuse until live).
- `inject-steer.sh` nonzero exit -> do not assume sent; surface evidence;
  auto-inject treats as failed and notifies, approval path returns to the human.

## Discipline (the invariants)

- Everything read-only; never modify the target repo's tracked files or its
  transcript.
- The overseer only observes and notifies the human; it never drives Codex on
  its own beyond the narrow auto-inject whitelist, and never without a
  verified-live phone channel.
- No keystrokes to Codex except through `inject-steer.sh` under the gate.
- No notification on a clean finding beyond the per-tick heartbeat; alerts are
  reserved for findings, terminal conditions, and post-hoc auto-inject notices.
- Deep-diving a fundamental design is not drift -- do not cry wolf on
  foundational work.

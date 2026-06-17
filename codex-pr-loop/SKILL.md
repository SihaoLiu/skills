---
name: codex-pr-loop
description: >-
  Iteratively drives the current branch's pull request to a clean Codex review.
  Reads Codex's PR-body reaction signals (thumbs-up meaning approved,
  eyes meaning still reviewing, new review comments meaning findings),
  verifies any findings locally, fixes real bugs with one or more commits,
  pushes everything in a single push, and posts a comment that re-triggers
  a full Codex review. After the first pass it schedules a recurring
  /loop 15m tick that re-runs the same check-fix-trigger cycle, and deletes
  that cron once Codex signals approval. Use when you want to babysit a PR
  against Codex feedback until it has no more findings. Triggers on
  "codex pr loop", "drive PR to green with codex", "iterate codex review",
  or requests to auto-iterate with Codex until approval.
user-invocable: true
---

# Codex PR Loop

A session-scoped workflow that keeps iterating on the current branch's PR
until the Codex reviewer has no more findings. The main session watches the
PR, verifies each finding against the local tree, commits fixes, pushes them
as a single batch, asks Codex to re-review, and repeats on a 15-minute
interval until Codex signals approval.

## When To Use

- Your working branch already has an open remote PR.
- Codex is configured to review PRs on this repository (either automatically
  on open, or via `@codex` mentions).
- You want an unattended loop that addresses Codex findings as they arrive
  and stops on its own once the PR is clean.

## When NOT To Use

- No remote PR exists yet. Open one first -- do not use this skill to create
  a PR.
- You need the loop to survive across sessions. `/loop` is session-scoped;
  use Routines, Desktop scheduled tasks, or GitHub Actions for durable
  schedules.
- The repository does not have Codex review wired up. Without Codex activity
  the termination condition never fires.

## Core Signals

Codex communicates through GitHub reactions on the PR body and through
review comments. This skill treats those signals as a small state machine:

| Signal on PR body                 | Meaning                                  | Action                                                                 |
|-----------------------------------|------------------------------------------|------------------------------------------------------------------------|
| Thumbs-up reaction (`+1`)         | Codex has reviewed and has no findings    | Terminate: delete the recurring cron (if any) and exit the skill       |
| Eyes reaction                     | Codex is actively reviewing right now    | Do not edit files this tick; schedule the loop and wait for next tick  |
| Review comments with new findings | Codex posted findings that need action   | Verify locally, fix, one batched push, comment to re-trigger review    |
| None of the above yet             | PR is fresh, Codex has not started       | Post the initial `@codex` trigger, then schedule the loop              |

"Review comments" means comments authored by the Codex bot on the PR
timeline (issue comments) or on code (review comments) that describe
problems. Approval reviews and non-bot chatter are ignored.

## Preconditions (check every tick)

Run these first. If any fails, log the reason and skip the tick rather than
pushing broken state:

1. Inside a git repository.
2. Current branch has an upstream set (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds).
3. `gh` is installed and authenticated (`gh auth status`).
4. A remote PR exists for the current branch (`gh pr view --json number,url,state,body`).
5. The PR state is `OPEN`. If it is `MERGED` or `CLOSED`, terminate.

Store the PR number, repo owner, and repo name once at the top of the tick
and reuse them for all `gh api` calls.

## One-Tick Workflow

One tick = one pass of the loop. The first invocation of the skill runs one
tick immediately, then schedules the cron. Every scheduled firing runs one
tick with the same logic.

### Step A -- Read the PR state

- `gh pr view --json number,state,body,url,headRefName,isDraft`
- `gh api repos/{owner}/{repo}/issues/{number}/reactions` -- list all
  reactions on the PR body. Collect the set of `content` values (for example
  `+1`, `eyes`, `heart`).
- Identify the Codex bot author login used in this repo. Typical values are
  `chatgpt-codex-connector[bot]`, `codex[bot]`, or `openai-codex[bot]`.
  Detect it dynamically by scanning authors of the issue comments and
  review comments that match `*[bot]` and containing the word `codex`
  (case-insensitive). If multiple candidates appear, prefer the one that
  actually authored a reaction on the PR body.
- `gh api repos/{owner}/{repo}/issues/{number}/comments` and
  `gh api repos/{owner}/{repo}/pulls/{number}/comments` -- fetch issue and
  review comments. Filter to those authored by the detected Codex bot.

### Step B -- Decide based on signals

Evaluate in this exact order and take the first matching branch:

1. **Thumbs-up reaction present on the PR body** -> **Terminate.**
   Delete the recurring cron (`CronList` to find the id whose prompt is
   this skill, then `CronDelete`), report the outcome, and stop. Do not
   push, do not comment, do not fix.

2. **Eyes reaction present and no new unaddressed Codex findings** ->
   Codex is still reviewing. Ensure the loop is scheduled (Step D), then
   return without edits.

3. **Codex bot has posted findings that are newer than the latest commit
   on the PR branch, or have not been addressed yet** -> Go to Step C.

4. **No reactions and no Codex comments yet** -> The PR is fresh and Codex
   has not started. Post an initial trigger comment:
   `@codex review this PR as entirety`. Then ensure the loop is scheduled
   (Step D) and return. Next tick will see the `eyes` reaction.

To detect "new/unaddressed findings", compare the latest Codex comment
timestamp to the latest commit timestamp on the PR head, and skip
comments that are already resolved or that only restate prior findings.
When in doubt, treat the comment as actionable and let the verification
step filter out false positives.

### Step C -- Verify and fix

For each finding:

1. Read the referenced file(s) at the current head. Do not trust line
   numbers literally -- they shift; locate by symbol, function, or
   surrounding context instead.
2. Decide whether the finding is real. If it is not (for example, Codex
   misread intent or pointed at code that has since been changed), record
   why in the tick's summary and skip it. Do not push a no-op.
3. If it is real, fix it in the smallest change that resolves the
   finding. Do not refactor unrelated code in the same pass.
4. Stage and commit each fix as its own focused commit. Commit messages
   must follow the user's rules: English only, no emoji, no CJK, no
   AI/agent authorship, no `Co-Authored-By: Claude *`, no literal `$(`,
   no literal `.humanize`, no progress terms like `Step`, `Phase`,
   `FIXED`, `round-i`, `wave-i`, or `AC-?`.

After all fixes are committed locally:

5. Run the repo's fast sanity checks if they exist (lint, type check, or
   a quick subset of tests). Fix any regressions before pushing.
6. **Push all commits in a single `git push`.** Never split the batch
   across multiple pushes -- Codex may re-trigger mid-batch and review
   an inconsistent snapshot.
7. Post one comment on the PR:
   `@codex review this PR as entirety`.
   Use `gh pr comment <number> --body "..."`. This forces Codex to
   re-scan the whole diff rather than reacting to individual commits.
8. Ensure the loop is scheduled (Step D).

If you cannot verify or fix a finding confidently (for example, the fix
needs a design decision), do not push a guess. Instead, comment on the PR
summarizing what was inspected and what is blocked, then leave the loop
scheduled so the next tick can pick up once the question is resolved.

### Step D -- Ensure the loop is scheduled

- Call `CronList`. If a recurring entry whose prompt invokes this skill
  already exists, do nothing.
- Otherwise, ask the `/loop` bundled skill to schedule the recurrence:
  invoke `/loop 15m /codex-pr-loop`. Per the `/loop` docs, passing a
  slash command as the prompt re-runs that packaged workflow each
  iteration. This yields a cron entry whose prompt is the skill itself,
  so next tick starts at Step A again.
- Report the cron id returned by `/loop` so the user can cancel manually
  if they want.

### Step E -- Report

End the tick with a short summary: which branch branch it ran on, which
signals it saw, which findings it addressed (or skipped and why), whether
it pushed, whether it re-triggered Codex, and whether the cron is live
or was deleted.

## Termination Rules

Terminate the loop (delete the cron and stop) in any of these cases:

- Thumbs-up (`+1`) reaction appears on the PR body and no newer Codex
  finding is outstanding.
- The PR is closed or merged.
- The current branch no longer has a tracking upstream or the remote PR
  has been deleted.
- Four consecutive ticks produce no progress (no new Codex activity and
  no local fix). Emit a report that the loop is stalling and let the user
  intervene; do not keep burning turns indefinitely.

Always delete the cron via `CronDelete` before exiting. Do not rely on the
seven-day scheduler expiry.

## Safety Rules

- Never force-push, never reset the branch, never amend published
  commits. Fixes are always new commits on top.
- Never skip git hooks (`--no-verify` is banned) unless the user has
  explicitly asked.
- Never push to `main` or `master`. This skill only operates on the
  current working branch's PR.
- Do not commit files that may contain secrets (`.env`, credentials,
  key material). If a finding seems to require one, stop and ask the
  user.
- Respect the user's global rules in `~/.claude/CLAUDE.md`: English-only
  files, no emoji or CJK in commit messages or PR bodies, no AI/agent
  authorship anywhere, no development-progress terminology.
- If `git commit` fails with `Author identity unknown`, configure with the
  user's identity using `git config` or other alias once and retry -- do not
  work around it by skipping hooks.
- If a pre-commit hook fails, fix the underlying issue and create a new
  commit. Never amend past a hook failure.

## Failure Modes To Watch

- **Wrong bot identity.** If the detected Codex bot is wrong, the tick
  will treat real findings as noise. Log the chosen author on the first
  tick and let the user correct it if needed.
- **Reaction cache lag.** GitHub reactions can take seconds to propagate.
  A tick that sees both `eyes` and `+1` should trust `+1` only if it is
  newer than the most recent Codex comment; otherwise wait one more tick.
- **Merge conflicts with base.** If the PR falls behind its base, a
  finding may be unreachable until the branch is rebased. Report the
  conflict and stop, do not attempt an automatic rebase in this skill.
- **Repeated findings.** If Codex keeps raising the same finding after
  a fix, the fix probably did not cover the root cause. Do not just
  revert and re-push -- instead, widen the investigation in the next
  tick and, if still stuck, report and stop.

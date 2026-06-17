---
name: superman
description: >-
  Superman development flow for large-scale feature development, combining
  superpowers brainstorming/planning with humanize RLCR execution loops and
  parallel sub-agent execution. Use this skill when starting major features,
  significant refactors, or complex multi-component implementations. Triggers
  on "superman flow", "superman mode", or requests for structured development
  with brainstorming, planning, sub-agent delegation, and code review loops.
user-invocable: true
---

# Superman Development Flow

Superman is an orchestration workflow that combines **superpowers** (brainstorming, planning) with **humanize** (RLCR loop, Codex review) for large-scale development. The main session acts purely as coordinator -- all implementation work is done by sub-agents in isolated git worktrees.

## Prerequisites

The following skills must be available (typically via plugins):

- `superpowers:brainstorming` -- design exploration and spec authoring
- `superpowers:writing-plans` -- implementation plan authoring
- `humanize:gen-plan` -- plan schema and format reference
- `humanize:start-rlcr-loop` -- iterative development with Codex review
- `humanize:ask-codex` -- independent expert review via Codex

## Workflow Overview

| Name | What happens | Codex role |
|------|-------------|------------|
| Prepare | Load skill context, create working directory | -- |
| Brainstorm | Design specs via `superpowers:brainstorming` | -- |
| Design Review | Codex reviews specs | Chief Architect |
| Plan | Implementation plans via `superpowers:writing-plans` | -- |
| Plan Review | Codex reviews plans | Chief Engineer |
| Launch Gate | Iterative GO/NO-GO from Opus sub-agent + Codex | Independent reviewers |
| Execute | RLCR loop + sub-agents in worktrees (sleep-monitor pattern) | Stage checkpoint reviewer |
| Finalize | Documentation handoff to codebase | -- |

---

### Prepare

Load the context of these dependencies -- they define the formats, schemas, and conventions used throughout:

- **humanize RLCR loop** -- the execution engine for actual development
- **superpowers brainstorming + writing-plans** -- the design and planning tools
- **humanize gen-plan** -- the plan schema reference

Create a working directory for this session:

```
temp/superman/<yyyy-mm-dd_hh-mm-ss>/
```

All specs and plans live here until the workflow completes.

### Brainstorm

Invoke `superpowers:brainstorming` to convert initial thoughts into design specifications.

- **Ask many questions.** This is the discovery stage -- probe requirements, constraints, edge cases, and tradeoffs thoroughly before writing anything.
- Produce one or more spec documents: `temp/superman/<timestamp>/spec-<topic>.md`
- Specs describe the **expected state** of the codebase after implementation -- architecture, interfaces, data flows, and behavior. Think of them as blueprints.
- Specs may eventually live in the codebase's documentation system after the work is done.

### Design Review (Codex as Chief Architect)

Invoke `humanize:ask-codex` to review the design specs.

Codex acts as **Chief Architect** in this review. Focus the review on:

- Whether the goals and priorities are clear and correctly captured
- Whether the overall architecture and component organization are sound
- Whether critical interfaces and data flows make sense
- Whether there are architectural risks or missing considerations

If the feedback is reasonable, revise the specs accordingly. If some feedback seems off, double-check with the user before accepting or rejecting it.

### Plan

Invoke `superpowers:writing-plans` to produce implementation plans from the reviewed specs.

Produce two types of plan documents:

- **Master plan** (`plan-master.md`): Coordinates sub-plans. Contains only the dependency graph, parallelism opportunities, checkpoints, and milestones across sub-plans. No implementation details.
- **Sub-plans** (`plan-<topic>.md`): Each covers a specific workstream with concrete implementation guidance. These are the working documents that drive sub-agents.

The key distinction between specs and plans:
- A **spec** is a blueprint -- it describes _what_ the code should look like and how it should behave. It persists as documentation.
- A **plan** is a construction guide -- it describes _how_ to build toward that state. It is disposable once the work is done and never enters version control.

### Plan Review (Codex as Chief Engineer)

Invoke `humanize:ask-codex` to review the implementation plans.

Codex acts as **Chief Engineer** in this review. Focus the review on:

- Whether the plans faithfully and completely implement what the design specs describe
- Whether the implementation approach is solid and practical
- Whether the task breakdown and dependency graph are correct
- Whether edge cases, testing strategy, and integration points are adequately covered

If the feedback is reasonable, revise the plans accordingly. If some feedback seems off, double-check with the user before accepting or rejecting it.

### Launch Gate (Iterative GO/NO-GO)

Starting an RLCR loop is expensive. Before committing, run an iterative readiness check:

1. **Launch two reviewers in parallel (background):**
   - An **Opus sub-agent** -- give it the full list of files under `temp/superman/<timestamp>/` and ask it to read everything, assess readiness, and return a GO or NO-GO verdict with rationale.
   - An **`humanize:ask-codex`** call -- same task: read all specs and plans, return GO or NO-GO with rationale.

2. **Evaluate verdicts:**
   - If **both** say GO -- proceed to Execute.
   - If **either** says NO-GO -- fix the identified issues in the specs/plans, then go back to (1) and re-launch both reviewers.

3. **Repeat** until both reviewers return GO in the same iteration.

This gate supplements (not replaces) the earlier Design Review and Plan Review. Those reviews catch issues incrementally as artifacts are produced; this gate is a final holistic readiness check across all specs and plans together, right before committing to execution.

### Execute

Invoke `humanize:start-rlcr-loop` starting with the master plan.

**Critical rules for execution:**

- **One RLCR round = the entire master plan is finished.** Do not write a round summary or attempt to exit after completing a single stage or milestone. All stages in the master plan must be completed within a single round. Only write `round-N-summary.md` and exit when you believe every sub-plan across every stage is done. Stage-level progress checks use manual `ask-codex` calls (see Milestone checkpoint below), not round boundaries.
- **All implementation work is done by sub-agents.** Trust the Opus sub-agents -- they are capable. Give them clear, complete instructions even for complex tasks.
- **Every sub-agent works in a git worktree.** This isolates their changes and prevents conflicts. Sub-agents must commit to their worktree branch only -- they must NOT merge into main or any shared branch. The main session explicitly instructs each sub-agent of this rule when dispatching tasks.
- **The main session only coordinates -- it does not implement.** Think of it as a tech lead: it delegates, supervises, reviews, merges, and handles cross-cutting concerns. Its responsibilities include but are not limited to:
  - Dispatch sub-plans to sub-agents (with explicit instruction: commit to your worktree branch, do not merge to main)
  - Monitor sub-agent progress
  - Review and validate completed work
  - Merge sub-agents' branches into main after review (only the main session merges to main)
  - Resolve conflicts between sub-agents' work
  - Make scheduling decisions based on the DAG (what to start next, what to wait on)
- **Do not stop the RLCR loop** until all plans (master and all sub-plans) are completed. When all work appears done, exit normally -- the RLCR stop hook automatically triggers a final Codex review at that point. This final auto-triggered review is distinct from the manual `ask-codex` stage checkpoints described below; do not conflate the two.

**Milestone checkpoint between stages:**

The master plan groups sub-plans into logical stages (e.g., foundation layer, infrastructure layer, integration layer) based on implementation dependencies and milestones. After completing all sub-plans within a stage and merging their branches:

1. **Verify locally** -- confirm the build passes, tests pass, and existing functionality is not broken.
2. **Invoke `humanize:ask-codex`** -- ask Codex to verify the stage completion. Provide it with: what was planned for this stage, what was actually implemented (commits, files changed), build/test results, and any deviations from the plan.
3. **Evaluate the verdict:**
   - If Codex confirms the stage is complete and sound -- proceed to the next stage.
   - If Codex identifies issues -- fix them before moving on. Re-run the Codex check after fixes.

This applies to every stage boundary in the master plan. Do not start a new stage until the previous one has passed its Codex checkpoint.

**Foreground sleep for monitoring sub-agents:**

After dispatching work to sub-agents in worktrees, the main session has nothing to do but wait. However, simply going idle can trigger RLCR stop hooks that incorrectly detect "stopping with incomplete tasks," wasting tokens in a loop of blocked stop attempts. To avoid this:

- **Run a foreground `sleep` command** (roughly 5 minutes) via Bash after dispatching sub-agents. This keeps the main session visibly active and prevents stop-hook interference.
- At the end of each sleep cycle, **check sub-agent progress** (e.g., list active worktrees, count new commits, check for diagnostic issues).
- **Repeat the sleep-then-check cycle** until all sub-agents have completed their work.
- Only then proceed to review results, merge branches, and continue the RLCR loop.

Example pattern:
```
echo "Monitoring sub-agents... $(date)" && sleep 300 && echo "=== Check at $(date) ===" && <progress check commands>
```

### Finalize

Once the RLCR Codex review confirms everything is good:

1. Ask the user whether to move the design specs out of `temp/` and into the codebase's documentation system under version control.
2. If yes, relocate the spec files and commit them.
3. Plans are never committed -- they served their purpose and can be discarded.

Nothing under `temp/` enters version control until the user explicitly approves this final step.

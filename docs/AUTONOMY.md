# Autonomous-work contract

Status: normative  
Scope: authority, durable coordination, and delegated Gate-AI finalization

This contract is scheduler- and runtime-agnostic. It does not install automation or grant authority merely by describing an execution capability.

## Durable operational authority

Fresh GitHub durable state is the operational authority over conversations, agent memory, caches, stale local files, and prior heartbeat results. New work selection uses the current published baseline and reconciles a legitimate active branch/PR rather than discarding it merely because the baseline advanced.

## Roles

- **HUMAN OWNER** owns product and material decisions, including observable behavior, significant architecture, data model, compatibility, security, persistence, data-loss risk, credentials, production, irreversible work, external contracts, business rules, relevant UX, Gate `HUMAN`, and changes to authority or merge policy.
- **CODEX WORKER** may execute authorized checkpoints, implement, test, repair in-scope failures, maintain affected documentation, work on branches, prepare authorized commits/PRs, respond to `AI_REWORK`, and update the queue. It may not self-approve, cross Gate `HUMAN`, merge or push directly to `main`, force-push, rewrite history, delete branches, alter policy, handle production or real data, perform destructive/irreversible operations, or persist secrets.
- **AI SUPERVISOR** reviews scope, correctness, diff, checks, documentation, architecture, evidence, and governance. Its durable decisions are exactly `AI_SUPERVISOR: APPROVED`, `AI_SUPERVISOR: AI_REWORK`, and `AI_SUPERVISOR: HUMAN_REQUIRED`. It decides semantically, does not write directly to `main`, and is not the finalizer.
- **Mechanical FINALIZER/HOST** performs only the safeguarded operational validation, closure, and delegated Gate-AI merge described below. It is not a second supervisor, semantic selector, or source of product or architecture decisions.
- **GITHUB** persists branches, commits, PRs, decisions, checks, and evidence; it does not itself confer authority.

## States, gates, and outcomes

The persistent checkpoint states are `READY`, `WORKING`, `AI_REVIEW`, `AI_REWORK`, `HUMAN_REQUIRED`, `BLOCKED`, and `DONE`. The permitted transitions are:

- `READY -> WORKING`
- `WORKING -> AI_REVIEW`
- `AI_REVIEW -> AI_REWORK`
- `AI_REWORK -> WORKING`
- `WORKING -> HUMAN_REQUIRED`
- `AI_REVIEW -> HUMAN_REQUIRED` when the AI SUPERVISOR escalates a material decision
- `HUMAN_REQUIRED -> READY` or `HUMAN_REQUIRED -> WORKING` only after a durable human resolution
- `WORKING -> BLOCKED`
- `BLOCKED -> READY` or `BLOCKED -> WORKING` only after the objective blockage has been durably resolved

`AI_SUPERVISOR: APPROVED` is semantic completion bound to the reviewed substantive HEAD; it is not a checkpoint state or a second semantic `AI_REVIEW -> DONE` decision. `DONE` is closure metadata mechanically materialized under the finalizer rules below.

Gate `AI` allows autonomous work and bounded delegated merge as specified here. Gate `HUMAN` cannot be crossed without an explicit durable owner resolution; AI approval never authorizes its merge. Gates are authorization metadata, not states.

`NO_OP` is a valid invocation result with no autonomous transition available; it is never a persistent checkpoint state. `BLOCKED` is a durable state for an objective external, technical, access, environment, or evidence dependency; record the cause and revalidation evidence. A launcher, scheduler, CLI, quota, lock, network, GitHub, or similar execution failure is a runtime failure, not automatically `BLOCKED`.

`HUMAN_REQUIRED` has a high threshold. Do not use it for naming, reasonable local organization, test structure, small refactors, equivalent choices, correctable defects, style, derived documentation, or reversible local work. Use it only for an undetermined material choice, including the categories owned by the HUMAN OWNER above or missing evidence that would require invention.

## Gate-AI approval, closure, and merge

An `AI_SUPERVISOR: APPROVED` bound to the legitimate PR's substantive reviewed HEAD means `semantic_done = true` and conditionally delegates `merge_authorized = true`. If that HEAD still records `AI_REVIEW`, then `merge_executable = false`: a separate finalizer must first derive a closure commit exactly from that substantive HEAD to materialize `DONE`.

The closure is not a checkpoint or semantic decision and needs no new semantic review only when its complete diff is strictly allowlisted: by default it may change only `docs/WORK_QUEUE.md`, only for operational closure metadata of the approved checkpoint. It cannot change code, tests, requirements, normative decisions, gates, objectives, dependencies, another checkpoint, or substantive behavior. A valid derived closure retains the original authorization.

Any later substantive change, or a later durable `AI_SUPERVISOR: AI_REWORK` or `AI_SUPERVISOR: HUMAN_REQUIRED`, invalidates the applicable approval. The finalizer must verify repository and PR identity, checkpoint and Gate `AI`, the approved substantive HEAD and its matching decision, no substantive later change or invalidating decision, PR legitimacy and mergeability, configured/required checks, no human-reserved condition, a wholly allowlisted closure, and a non-destructive operation. It must protect merge against HEAD movement with `expected_head_sha` or equivalent when available.

Failure of checks, mergeability, closure validation, or another operational precondition pauses finalization; it does not automatically make the checkpoint `BLOCKED`. Generic GitHub auto-merge is not authorized or enabled.

## Future selection rule

One valid heartbeat/invocation may process at most one checkpoint/PR principal unit. Its future priority is: `AI_REWORK`; approved Gate-AI work awaiting mechanical finalization; existing `WORKING`; `READY` Gate AI; `READY` Gate HUMAN (`NO_OP`); `AI_REVIEW` without a new decision (`NO_OP`); `HUMAN_REQUIRED` (do not cross); then `BLOCKED` (objectively revalidate).

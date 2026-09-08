# Foundational decisions

## D-001 — Adopt work-governance baseline

**Decision:** Adopt `Innlab-idi/work-governance` revision `2746015fddc02636772fedb9d8672b244cea0b4e` as the canonical organizational baseline.

**Consequence:** Its managed `AGENTS.md` block is retained verbatim except for the adopted revision. It is not a runtime dependency.

## D-002 — Public reusable capacity, separate consumer authority

**Decision:** This is a public reusable repository. It provides execution capacity, never authority; consumers explicitly adopt any delegation.

**Consequence:** Consumer product, architecture, roadmap, queue, gates, decisions, private context, and local authority remain local. No circular dependency with work-governance is created.

## D-003 — GitHub durable state and separated roles

**Decision:** Fresh durable GitHub state is operational authority. HUMAN OWNER, CODEX WORKER, AI SUPERVISOR, mechanical FINALIZER/HOST, and GitHub have the separate roles defined in `AUTONOMY.md`.

**Consequence:** Technical access does not expand authority; the worker cannot self-approve or merge `main`, and the supervisor is not the finalizer.

## D-004 — Delegated Gate-AI merge, never generic auto-merge

**Decision:** `AI_SUPERVISOR: APPROVED` bound to a substantive Gate-AI HEAD is semantic completion and conditional merge authorization. Generic GitHub auto-merge is neither enabled nor authorized.

**Consequence:** A distinct finalizer validates safeguards, creates only a strictly allowlisted closure where needed, and performs a protected merge. Gate HUMAN and human-reserved matters remain owner-only.

## D-005 — Binding, invalidation, and allowlisted closure

**Decision:** Approval binds the substantive reviewed HEAD. A later substantive change, `AI_REWORK`, or `HUMAN_REQUIRED` invalidates it. A closure derived exactly from that HEAD may only record approved checkpoint closure metadata in `docs/WORK_QUEUE.md`.

**Consequence:** Closure is not a new semantic decision; an invalid diff requires fresh review, and merge uses HEAD-movement protection when available.

## D-006 — Runtime failure is not BLOCKED

**Decision:** Runtime/execution failure and persistent checkpoint `BLOCKED` are different outcomes.

**Consequence:** Launcher, scheduler, CLI, quota, lock, GitHub, network, checks, mergeability, or finalization precondition failures do not automatically mark a checkpoint `BLOCKED`; `NO_OP` is also an invocation outcome, never a persistent state.

## D-007 — Portable architecture remains undecided

**Decision:** Governance is separated from worker, adapter, scheduler/runtime, and finalizer mechanics. The concrete technology and architecture are deferred to ARCH-01.

**Consequence:** No choice is made now for platform, language, CLI, code layout, configuration, lock, logs, scheduler, installation, or packaging. Prior VEVI prototype lessons are architecture inputs recorded in `EXECUTION.md`, not implementation decisions.

## D-008 — my-own-governance provenance is non-canonical

**Decision:** Record `rmolck/my-own-governance` revision `9a40aaf6ce5039ceb5d127d43ccbdf4964a61398` as conceptual/documentary design provenance, especially for Gate-AI/finalizer policy.

**Consequence:** It is neither a runtime dependency nor a second organizational baseline.

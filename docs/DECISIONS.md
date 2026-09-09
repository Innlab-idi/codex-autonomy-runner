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

**Decision:** Approval binds the substantive reviewed HEAD. A later substantive change, `AI_SUPERVISOR: AI_REWORK`, or `AI_SUPERVISOR: HUMAN_REQUIRED` invalidates it. A closure derived exactly from that HEAD may only record approved checkpoint closure metadata in `docs/WORK_QUEUE.md`.

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

## D-009 - Adopt Python control core with optional thin Windows launcher

**Decision:** In ARCH-02, the HUMAN OWNER adopted the ARCH-01 recommendation,
as durably recorded in GitHub issue #3: Python is the deterministic
host/control core, and an optional minimal Windows PowerShell launcher may be
used only for Windows or Task Scheduler invocation ergonomics. The launcher
does not own governance parsing, semantic prompts, Git/GitHub logic, recovery
semantics, result interpretation, or finalization logic.

**Consequence:** Python conceptually owns structured subprocess execution and
result handling; explicit UTF-8 boundaries; filesystem and changed-path
validation including untracked paths; host-side Git/GitHub mechanics;
per-repository locking; recovery; non-sensitive observability; and bounded
mechanical finalizer safeguards. Consumer repositories retain product policy,
queue, gates, decisions, context, delegation, and authority. Future
multi-repository orchestration remains separate and must not embed
consumer-private knowledge in this runner. This supersedes only D-007's
"architecture remains undecided" aspect; D-007's separation of governance
from execution mechanics remains in effect.

This decision approves the architecture choice only. Python and supported
platform versions; provisioning, packaging, and installation; configuration
and structured-result formats; lock implementation; recovery-record
persistence; observability/logging sink; Git/GitHub library or CLI; code/module
layout; scheduler details; and multi-repository orchestrator design remain
intentionally deferred.

## D-010 - Adopt least-semantic actor ownership and strict worker/host separation

**Decision:** The HUMAN OWNER adopts the least-semantic actor principle:
each operation belongs to the least-semantic actor that can perform it safely,
and technical capability never expands authority. AI COORDINATOR and AI
SUPERVISOR are separate logical roles; the former performs repository-local
semantic coordination and the latter performs review only. CODEX WORKER is
worktree-only. HOST owns strictly authorized mechanical Git/GitHub mutation.
FINALIZER is a separate, deterministic, non-LLM mechanical component, and the
future RUNNER is minimal mechanical runtime infrastructure. Stable actor
instructions are a future versioned-repository principle; external timing and
empirical cadence tuning are future runtime principles. MULTI-REPO
ORCHESTRATOR remains external and separate from repository-local coordination.

**Consequence:** D-010 clarifies and amends D-003's combined worker/finalizer/
host role description and D-009's conceptual host/control responsibilities:
it does not rewrite their history, weaken HUMAN boundaries, or revoke D-009's
Python-core adoption. The worker receives no branch, staging, commit, push,
PR, GitHub metadata, or `.git` write authority. Supervisor decisions remain
exactly `AI_SUPERVISOR: APPROVED`, `AI_SUPERVISOR: AI_REWORK`, and
`AI_SUPERVISOR: HUMAN_REQUIRED`, are explicitly HEAD-bound, and cannot be
published when review is unreliable. FINALIZER may merge only a previously
authorized Gate-AI unit after fresh-state validation as a mechanical pre-worker
phase; a subsequent refresh may still authorize one worker execution. HOST,
not the worker, makes worker changes durable and review-ready through required
authorized publication. RUNNER does not become a governance engine. External
GitHub metadata that did not occur must never be represented by a Git-tree
commit. This owner decision was informed by later lessons observed in
`rmolck/my-own-governance`, including GOV-007/GOV-008/GOV-009, solely as
non-canonical conceptual provenance and never as a runtime dependency or
adoption of unapproved behavior.

## D-011 - Adopt adaptive roadmap reconciliation and just-in-time planning

**Decision:** The HUMAN OWNER approved this decision durably in issue #24.
Each consumer's roadmap is adaptive medium-term direction, not a compiled
execution queue. Within a consumer's durable policy, AI COORDINATOR may make
only conservative, traceable changes to future roadmap structure from durable
evidence, completed work, and already approved decisions. Material choices
remain with the HUMAN OWNER. Roadmap reconciliation and next-phase checkpoint
decomposition are separate semantic planning work requiring an explicitly
authorized planning transition; only the next currently valid phase may be
decomposed just in time. RUNNER remains mechanical, materialized checkpoint
contracts remain stable pending explicit durable reconciliation, and consumer
repositories retain their own roadmap, queue, evidence, requirements,
decisions, gates, delegation, and private context.

**Consequence:** Future roadmap phases are not precompiled speculatively into
the work queue: later durable evidence may reorder, replace, split, merge, or
remove them. The coordinator may not invent work, requirements, evidence, or
HUMAN-reserved choices, and may not silently alter an existing checkpoint's
scope. The `vevi-exporter` P5-PREP experience is only non-normative conceptual
provenance; its concrete structure is neither required nor a dependency here.

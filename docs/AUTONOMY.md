# Autonomous-work contract

Status: normative  
Scope: authority, durable coordination, and delegated Gate-AI finalization

This contract is scheduler- and runtime-agnostic. It does not install automation or grant authority merely by describing an execution capability.

## Durable operational authority

Fresh GitHub durable state is the operational authority over conversations, agent memory, caches, stale local files, and prior heartbeat results. New work selection uses the current published baseline and reconciles a legitimate active branch/PR rather than discarding it merely because the baseline advanced.

## Roles

- **Least-semantic actor principle.** Each operation belongs to the least-semantic actor that can perform it safely. Technical capability never expands authority.
- **HUMAN OWNER** owns product and material decisions, including observable behavior, significant architecture, data model, compatibility, security, persistence, data-loss risk, credentials, production, irreversible work, external contracts, business rules, relevant UX, Gate `HUMAN`, and changes to authority or merge policy.
- **AI COORDINATOR** is a repository-local logical role, not an implementation provider. From fresh durable state it reconciles contradictions and legitimate active work, selects or frames at most one authorized work unit under the repository's durable policy, and prepares the necessary context/instruction for other actors. Its bounded roadmap and planning authority is defined below. When it has both capability and authorization, it may perform coordination and GitHub metadata operations; it never represents an unperformed external GitHub operation with a Git-tree commit. An inability to update GitHub metadata is an operational failure that must be recorded truthfully. It does not replace the HUMAN OWNER. One intelligent agent may represent AI COORDINATOR and AI SUPERVISOR at different times, but their logical authorities never mix.
- **CODEX WORKER** is worktree-only. It may read authorized context, modify only permitted working-tree files, implement, run permitted checks, repair in-scope `AI_REWORK`, and report technical evidence and changes. It may not create or change branches, stage, commit, push, create or modify PRs, modify GitHub metadata, write `.git`, merge, self-approve, cross Gate `HUMAN`, alter policy, handle production or real data, perform destructive/irreversible operations, or persist secrets.
- **AI SUPERVISOR** receives an already selected, review-ready checkpoint/PR. It does not implement fixes, select or initiate another checkpoint, schedule, finalize, invent requirements, or invent evidence. It reviews repository and checkpoint identity; gate/state; branch/PR and exact HEAD; real diff and authorized scope; normative requirements; checks/evidence; relevant mergeability; and HUMAN boundaries. Its durable decisions are exactly `AI_SUPERVISOR: APPROVED`, `AI_SUPERVISOR: AI_REWORK`, and `AI_SUPERVISOR: HUMAN_REQUIRED`. It uses `AI_REWORK` for worker-correctable defects or evidence, and `HUMAN_REQUIRED` only for a genuinely HUMAN OWNER-reserved decision. If reliable review is prevented by tooling or access, it publishes no semantic decision. A published decision is HEAD-bound: obtain HEAD A, review A, read the PR HEAD again immediately before publication, and publish a decision bound to A only if it remains A; otherwise review the new HEAD. A decision is never implicitly retargeted to a later HEAD.
- **HOST** is the mechanical privileged role around the worker: freshness/fetch and mechanical reconciliation; preparation or continuation of a legitimate branch; post-worker filesystem validation including untracked paths and exact permitted paths; staging; commit; non-force push and publication verification; mechanical PR creation/update; and other strictly authorized Git/GitHub operations. It does not decide product, architecture, semantic scope, approval, or Gate `HUMAN`; mechanical capacity does not make it a semantic authority. Worker operation must remain possible without GitHub credentials, without network when sufficient, and without `.git` write access.
- **FINALIZER** is a logically separate, purely mechanical component whose target design permits deterministic operation without an LLM. In a wake it is a pre-worker phase: it may consume fresh durable state; verify an existing Gate-AI authorization and PR/checkpoint/HEAD/decision identity; detect invalidating decisions or changes; validate checks, mergeability, and absence of a HUMAN-reserved condition; derive only allowlisted closure when necessary; use expected-HEAD protection or equivalent; and perform the previously authorized merge. It cannot approve, repair evidence, reinterpret a supervisor decision, select or invoke semantic worker work, implement, or initiate another checkpoint. A successful pre-finalization is followed by fresh refresh/reconciliation and does not itself consume the wake's one semantic worker opportunity.
- **RUNNER** is the future minimal mechanical runtime: lock, technical preflight, durable-state refresh boundaries, invocation, result/exit capture, technical observability, and finalizer invocation. It must not duplicate roadmap, checkpoint priorities, product or architecture decisions, supervisor judgment, or consumer-private policy.
- **MULTI-REPO ORCHESTRATOR** is a future component outside this repository. It may select which repository receives an execution opportunity according to its own configuration/priorities; it is not AI COORDINATOR, does not select consumer-internal checkpoints, and does not duplicate each consumer's durable internal policy. This repository contains no consumer-repository inventory for it.
- **GITHUB** persists branches, commits, PRs, decisions, checks, and evidence; it does not itself confer authority.

Stable, reusable actor instructions will be versioned in the repository in a future implementation. Future adapters/prompts are to be minimal bootstraps to those instructions and current durable state; their names and structure remain deferred and create no new authority.

## Adaptive roadmap and planning

A consumer roadmap expresses medium-term direction from current durable knowledge;
it is not a compiled, immutable plan and does not itself authorize execution or
automatic creation of `READY` checkpoints. Within its repository-local durable
policy, the AI COORDINATOR may conservatively reconcile only future roadmap
structure when the result is traceable to durable evidence, completed work, or
already approved decisions. It may then reorder, split, merge, remove, or add
technically necessary future phases, or change their future dependencies and
sequence. It may not invent product, requirements, evidence, speculative work,
or a material choice absent from the durable record.

Roadmap reconciliation and checkpoint decomposition are semantic planning work,
not RUNNER work. They require an explicitly authorized planning
transition/checkpoint. After fresh reconciliation, that transition may
decompose only the next currently valid phase into its needed checkpoints,
dependencies, gates, evidence requirements, done criteria, scope limits, and
minimum ordering; it does not implement that phase simultaneously. Later
phases remain at roadmap level because later evidence may change or remove
them. Naming and mechanics of planning transitions remain deferred.

The HUMAN OWNER retains every material category already assigned in the Roles
section. When reconciliation would require such an unresolved choice, the AI
COORDINATOR stops at the applicable HUMAN boundary, including `HUMAN_REQUIRED`
when the existing contract requires it. This authority preferentially affects
future work not yet materialized in `WORK_QUEUE`. A materialized checkpoint's
durable contract, including an active worker's scope, is never silently
rewritten, invalidated, or discarded; newly conflicting evidence requires
explicit durable reconciliation. Evidence obtained during a phase may still
reconcile later unmaterialized roadmap structure without changing that phase's
active checkpoint.

RUNNER remains minimal mechanical infrastructure and cannot interpret or
restructure a roadmap, invent checkpoints, choose a semantic next phase, or
perform product or architecture planning. Each consumer repository retains its
own roadmap, queue, gates, evidence, requirements, decisions, delegation, and
private context. MULTI-REPO ORCHESTRATOR remains external and may select a
repository only, never its internal roadmap or checkpoints.

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

FINALIZER pre-pass is a mechanical phase, not a competing entry in semantic work selection. After its required fresh refresh/reconciliation, the AI COORDINATOR may select at most one CODEX WORKER execution in a wake using this durable repository-local priority: `AI_REWORK`; existing `WORKING`; `READY` Gate AI; `READY` Gate HUMAN (`NO_OP`); `AI_REVIEW` without a new decision (`NO_OP`); `HUMAN_REQUIRED` (do not cross); then `BLOCKED` (objectively revalidate). The order or multiplicity of simultaneously finalizable PRs remains deferred to future FINALIZER implementation.

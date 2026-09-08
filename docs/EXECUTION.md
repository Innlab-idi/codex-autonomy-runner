# Portable execution contract

Status: normative  
Scope: execution boundaries

This contract applies [AUTONOMY.md](AUTONOMY.md); it does not duplicate or redefine its authority, states, gates, selection rule, or finalization policy. It requires no particular operating system, language, agent, scheduler, hosting service, lock, configuration format, or packaging model.

## Boundaries

The conceptual boundary is:

```text
AI COORDINATOR -> CODEX WORKER -> HOST -> FINALIZER/RUNNER
```

The repository-local AI COORDINATOR semantically reconciles fresh durable state, frames at most one authorized unit under the adopted consumer policy, and supplies the worker's authorized context. CODEX WORKER performs only permitted worktree changes and returns technical evidence; it has no Git or GitHub mutation authority. HOST performs the narrowly authorized mechanical Git/GitHub operations and validates actual filesystem changes, including untracked paths and exact permitted paths. FINALIZER remains separate from all semantic roles. The future RUNNER invokes these mechanical boundaries without duplicating consumer policy or supervisor judgment.

No actor may use a commit to claim an external GitHub metadata operation that did not occur. A metadata access failure remains an observable operational failure, not a Git-tree substitute.

## Invocation and result contract

One invocation evaluates current durable state and may process at most one checkpoint/PR principal unit, including one approved operational finalization. It must distinguish: a completed authorized transition; valid `NO_OP`; a durably recorded checkpoint `BLOCKED`; runtime/execution failure; and interrupted or untrustworthy execution. The portable interface must distinguish valid completion from execution failure without parsing prose.

Before action, the AI COORDINATOR and HOST must resolve the repository and baseline identity, applicable normative instructions and queue, relevant branches/PRs and HEADs, durable supervisor decisions, and relevant checks/evidence. Fresh durable state is required for new-work selection; valid active work must be reconciled, not recreated or discarded.

Technical preflight and mutual exclusion are implementation concerns. Failure there, or in a launcher, scheduler, CLI, quota, lock, GitHub/network access, or execution mechanism, does not by itself mutate a checkpoint to `BLOCKED`. HOST must validate actual filesystem changes, including untracked paths, instead of trusting only a worker summary. It must verify exact changed paths before staging, committing, or publishing; publication must be non-force and verified against the remote.

## Reference phase sequence

Future implementation follows this architectural sequence, without prescribing its mechanism:

```text
lock -> fresh refresh -> finalizer pre-pass -> worker if still authorized
-> await worker -> fresh refresh -> finalizer post-pass -> record result -> unlock
```

If the pre-pass finalizes a unit, the invocation ends and starts no other checkpoint. Worker termination alone is never sufficient for finalization. Each finalizer pass uses fresh durable state. If the post-worker refresh fails, no post-pass may run and the result is runtime/execution failure. If the post-pass fails, the result is likewise runtime/execution failure rather than a concealed successful worker result. An iteration is not valid completion when a required reliability boundary failed.

## Privilege separation, timing, and observability

GitHub/network access and credentials may remain in the host. A worker must be able to operate without GitHub credentials, without network when sufficient, and without write access to `.git`. Credentials must never be passed to the worker unnecessarily.

The execution layer records enough non-sensitive evidence to diagnose an invocation: temporal identity, repository identity, observed baseline and relevant HEADs when known, result, execution status, and technical error. It must not record secrets, credentials, private data, or sensitive infrastructure details. The runtime should externally measure worker/Codex duration with a monotonic clock where possible, and should also measure complete iteration duration where possible. Observability must support later analysis of duration distributions, failures, outcomes, and lock contention. Scheduler frequency is to be tuned empirically from sufficient evidence, including median, p90/p95, maxima, and operational margin rather than only an average. Exhaustive per-run telemetry must not create a `main` commit for each heartbeat; raw runtime evidence is local/operational by default and may be aggregated only in sanitized, bounded form when useful. Log format, location, schema, retention, cadence, locking implementation, and logging system remain deferred.

## Architecture inputs, not implementation decisions

ARCH-01 must consider prior prototype lessons: explicit UTF-8 handling; stable collection-returning interfaces; safe prompt transport rather than fragile shell interpolation; inclusion of untracked files in filesystem validation; interruption recovery without needless agent reruns; host-side path validation; privilege separation around `.git` and network access; and non-force remote publication verification. These are requirements to assess, not selections of PowerShell, Python, a shell, scheduler, logging system, locking mechanism, CLI, or file layout.

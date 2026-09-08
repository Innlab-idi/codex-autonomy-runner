# Portable execution contract

Status: normative  
Scope: execution boundaries

This contract applies [AUTONOMY.md](AUTONOMY.md); it does not duplicate or redefine its authority, states, gates, selection rule, or finalization policy. It requires no particular operating system, language, agent, scheduler, hosting service, lock, configuration format, or packaging model.

## Boundaries

The conceptual boundary is:

```text
governance-aware worker -> execution adapter -> scheduler/runtime
```

The worker interprets the adopted consumer policy and performs authorized semantic selection and checkpoint work. The adapter translates one portable invocation to a concrete worker interface, supplies only needed resolved context, and faithfully returns technical evidence and completion status. The scheduler/runtime wakes, establishes mutual exclusion, performs technical preflight, invokes the adapter, records operational observability, and stops.

A mechanical finalizer/host is separate from the supervisor and worker's semantic role. It may perform only the Gate-AI closure and merge safeguards specified in `AUTONOMY.md`.

## Invocation and result contract

One invocation evaluates current durable state and may process at most one checkpoint/PR principal unit, including one approved operational finalization. It must distinguish: a completed authorized transition; valid `NO_OP`; a durably recorded checkpoint `BLOCKED`; runtime/execution failure; and interrupted or untrustworthy execution. The portable interface must distinguish valid completion from execution failure without parsing prose.

Before action, the host must be able to resolve repository and baseline identity, applicable normative instructions and queue, relevant branches/PRs and HEADs, durable supervisor decisions, and relevant checks/evidence. Fresh durable state is required for new-work selection; valid active work must be reconciled, not recreated or discarded.

Technical preflight and mutual exclusion are implementation concerns. Failure there, or in a launcher, scheduler, CLI, quota, lock, GitHub/network access, or execution mechanism, does not by itself mutate a checkpoint to `BLOCKED`. The host must validate actual filesystem changes, including untracked paths, instead of trusting only a worker summary. It must verify exact changed paths before staging, committing, or publishing; publication must be non-force and verified against the remote.

## Privilege separation and observability

GitHub/network access and credentials may remain in the host. A worker must be able to operate without GitHub credentials, without network when sufficient, and without write access to `.git`. Credentials must never be passed to the worker unnecessarily.

The execution layer records enough non-sensitive evidence to diagnose an invocation: temporal identity, repository identity, observed baseline and relevant HEADs when known, result, execution status, and technical error. It must not record secrets, credentials, private data, or sensitive infrastructure details.

## Architecture inputs, not implementation decisions

ARCH-01 must consider prior prototype lessons: explicit UTF-8 handling; stable collection-returning interfaces; safe prompt transport rather than fragile shell interpolation; inclusion of untracked files in filesystem validation; interruption recovery without needless agent reruns; host-side path validation; privilege separation around `.git` and network access; and non-force remote publication verification. These are requirements to assess, not selections of PowerShell, Python, a shell, scheduler, logging system, locking mechanism, CLI, or file layout.

# ARCH-01 implementation architecture assessment

Status: assessment and recommendation; not an adopted decision
Scope: future reusable autonomy runner implementation architecture

Subsequent decision note: ARCH-01 remains the historical assessment that made
this recommendation. The HUMAN OWNER later adopted it in ARCH-02, as durably
recorded in GitHub issue #3; the canonical adopted decision is D-009 in
`DECISIONS.md`.

## 1. Context and constraints

This assessment concerns technical capacity only. `AUTONOMY.md` remains the
authority for roles, states, gates, selection priority, approval binding, and
delegated Gate-AI closure. Consumer repositories continue to own their product
policy, queue, gates, decisions, context, and delegation. `work-governance`
remains the canonical organizational baseline.

The future runner must use fresh durable GitHub state as operational authority,
process at most one checkpoint/PR principal unit per invocation, and keep
runtime failure distinct from a durable `BLOCKED` checkpoint. It must not
become a second governance engine: it may read and mechanically apply the
consumer's adopted contract but must not invent consumer policy or semantic
state.

The host/control layer needs deterministic validation around a semantic worker:
the worker may not need GitHub credentials, network access, or `.git` write
access; host publication is non-force and fresh-state verified; and finalizing
an approved Gate-AI item requires the contractual, strictly allowlisted closure
and expected-HEAD merge protection. No runtime implementation is defined by
this document.

## 2. Prototype evidence

A local, uncommitted prototype reference was inspected solely for architectural
lessons. No paths, identities, credentials, prompts, or operational details
from it are reproduced here. Together with the already normative inputs in
`EXECUTION.md`, it provides the following evidence:

- Windows PowerShell 5.1 needs deliberate UTF-8 and native-process handling.
- Shell interpolation, here-strings, and embedded large prompts are fragile.
- PowerShell collection-return behavior can make a one-item result a scalar,
  which is a poor boundary for deterministic orchestration.
- A plain Git diff omits untracked files; host validation must inspect actual
  changed paths in the filesystem and repository state.
- Interruptions need a durable, inspectable recovery point so semantic work is
  not rerun merely because a later operational step was interrupted.
- Workers may be isolated from `.git`, network, and GitHub credentials, while
  the host can perform the narrowly privileged Git/GitHub operations.

These are evidence and requirements, not a technology decision or a claim
that the prototype is canonical.

## 3. Candidate architectures

### A. Windows PowerShell 5.1 core

Use Windows PowerShell 5.1 for the control layer, worker adapter, validation,
Git/GitHub operations, recovery, and eventual scheduling integration.

### B. PowerShell 7 core

Use PowerShell 7 for those same cross-platform control responsibilities, with
Windows integration expressed directly in PowerShell.

### C. Python core with a thin Windows PowerShell launcher

Use Python for deterministic control and host mechanics. If Windows Task
Scheduler ergonomics need a script entry point, use a very small PowerShell
launcher whose responsibility ends at locating and invoking the configured
Python entry point and faithfully returning its exit status. The launcher does
not own prompts, policy, Git/GitHub logic, recovery, or result interpretation.

### D. Hybrid with PowerShell 7 core and Python helper components

Use PowerShell 7 as the primary control plane while introducing Python for
selected difficult operations. This is distinct only because it splits the
control-plane data model and error semantics across languages.

## 4. Comparison matrix

Ratings are relative to this checkpoint's constraints: strong, adequate, or
weak. They are an architectural assessment, not benchmark measurements.

| Criterion | A. Windows PowerShell 5.1 core | B. PowerShell 7 core | C. Python core + thin Windows launcher | D. PS7 core + Python helpers |
| --- | --- | --- | --- | --- |
| Correctness and determinism | Weak: legacy shell semantics add edge cases | Adequate | Strong: one explicit control-plane model | Adequate: cross-language boundaries add cases |
| Testability | Weak to adequate | Adequate | Strong | Adequate |
| UTF-8 and text robustness | Weak: requires persistent care | Adequate | Strong with explicit text boundaries | Adequate |
| Native process handling | Weak: awkward error/exit behavior | Adequate | Strong | Adequate |
| JSON and structured data | Adequate | Adequate | Strong | Adequate |
| Stable collection/data semantics | Weak: scalar/collection ambiguity | Adequate | Strong | Adequate |
| Filesystem and changed-path validation | Adequate but shell-sensitive | Adequate | Strong | Adequate |
| Git/GitHub host operations | Adequate | Adequate | Strong | Adequate |
| Privilege separation | Adequate | Adequate | Strong | Adequate |
| Worker sandbox compatibility | Adequate | Adequate | Strong | Adequate |
| Interruption and recovery | Adequate | Adequate | Strong | Adequate |
| Mutual exclusion and locking | Adequate | Adequate | Strong | Adequate |
| Observability | Adequate | Adequate | Strong | Adequate |
| Windows Task Scheduler integration | Strong | Strong | Strong with minimal launcher | Strong |
| Cross-platform portability | Weak | Strong | Strong | Strong |
| Ordinary-Windows installation | Strong: inbox runtime | Adequate: separate runtime | Adequate: Python must be provisioned | Weak to adequate: two runtimes |
| Dependency burden | Strong: no new runtime | Adequate | Adequate | Weak |
| Maintainability | Weak: prototype failure modes concentrate here | Adequate | Strong | Weak to adequate |
| Multi-repository evolution | Adequate | Strong | Strong | Adequate |
| External, explicit UTF-8 prompts/configuration | Weak to adequate | Adequate | Strong | Adequate |
| Independent finalizer testing | Adequate | Adequate | Strong | Adequate |
| Avoiding duplicated governance semantics | Adequate | Adequate | Strong: narrow typed control boundary | Adequate |

## 5. Recommended architecture

Recommend **a Python core with an optional, very thin Windows PowerShell
launcher**. This is a recommendation for later human adoption, not an accepted
decision and not authorization to implement it.

Python should conceptually own deterministic runtime mechanics: subprocess
execution and structured results; explicit UTF-8 input/output; filesystem and
changed-path validation including untracked paths; host-side Git/GitHub command
orchestration; per-repository locking; recovery records; non-sensitive
observability; and the mechanical finalizer safeguards. Its inputs and outputs
should be explicit structured data rather than shell interpolation or prose
parsing. Prompts and configuration should remain external, explicit UTF-8
inputs instead of source-embedded large strings.

The optional launcher exists only for Windows invocation and Task Scheduler
ergonomics. It should not parse consumer governance, construct semantic
prompts, handle Git/GitHub credentials, decide results, or duplicate recovery
logic. Direct invocation remains possible on other supported platforms.

This recommendation best isolates the prior prototype's PowerShell 5.1
failure modes without giving the runner policy authority. It also gives the
eventual finalizer a deterministic, independently testable mechanical boundary
from semantic worker execution.

## 6. Why the alternatives are weaker

Windows PowerShell 5.1 is attractive because it is commonly present on Windows,
but the prototype evidence directly identifies text, native-process, and
collection-semantics hazards in its role as a core control plane. Mitigations
are possible, but would make correctness depend on repeated local discipline
rather than a simpler data model.

PowerShell 7 removes some legacy constraints and improves portability, but it
still keeps the primary orchestration in a shell-oriented environment. It is a
reasonable fallback if the later owner values a one-language PowerShell estate
above the assessed maintainability and testability advantages, but it is not
the strongest choice on the evidence available.

The mixed PS7-core/Python-helper approach has no compensating advantage over a
Python core. It expands runtime and boundary complexity while leaving unclear
which language is authoritative for result, recovery, and validation semantics.

## 7. Proposed conceptual component boundaries

The following are conceptual responsibilities, not a module, class, file, or
wire-format design:

1. **Invocation boundary:** receives a selected repository path/ref and a
   bounded invocation request; does not select unrelated repositories. After
   reconciliation, it selects exactly one principal checkpoint/PR action.
2. **Host/control layer:** obtains fresh durable state, takes a per-repository
   lock, reconciles legitimate active work, selects the one principal unit,
   invokes either an adapter or an eligible mechanical finalizer, validates
   observable results, records operational evidence, and stops.
3. **Worker adapter:** supplies only needed resolved consumer context to the
   semantic worker and returns structured technical completion evidence. It
   does not translate technical failure into a policy state.
4. **Semantic worker:** interprets the consumer's adopted policy and performs
   only authorized checkpoint work. It remains separate from host credentials
   and need not write `.git`.
5. **Mechanical finalizer:** separately verifies the contractual Gate-AI
   approval and exact substantive HEAD, permits only allowlisted closure,
   validates merge preconditions, and performs a protected non-force merge.
6. **External integration boundary:** Git/GitHub and scheduler integrations
   are host-side technical dependencies, never governance authorities.

Branch identity is resolved from fresh durable state before any creation. If a
legitimate active PR/branch exists for the selected checkpoint, the runner
continues exactly that branch and never creates a replacement merely because a
locally derived name differs. Only for a genuinely new `READY` checkpoint with
no legitimate active branch/PR may it deterministically derive a branch
identity from the checkpoint identifier under a documented naming rule. A form
such as `codex/<normalized-checkpoint-id>` (for example, `ARCH-01` to
`codex/arch-01`) illustrates the rule but is non-binding; its exact syntax and
configuration remain deferred implementation details. Neither an orchestrator
nor a semantic worker invents arbitrary branch names.

## 8. Trust and privilege boundaries

The HUMAN OWNER retains material architecture, product, policy, credential,
production, and Gate-HUMAN authority. The consumer durable policy constrains
the worker and host; it is not rewritten by either. The AI SUPERVISOR provides
the defined durable semantic decision but is not the finalizer.

The host is the smallest privileged boundary: it may hold GitHub access and
perform narrowly authorized Git/GitHub operations after fresh-state validation.
Credentials stay host-side and are not sent to the worker. The worker receives
the minimum context necessary and may operate without network, credentials, or
`.git` write access. The finalizer is a bounded host capability, not a general
merge facility and never a substitute HUMAN OWNER decision.

## 9. Data and control flow for one invocation

```text
selected consumer repository + requested ref
  -> host: acquire per-repository exclusion and fresh-state preflight
  -> host: reconcile consumer-local durable policy, queue, PR/HEAD, decisions,
     checks, and any legitimate active branch/PR
  -> host: select exactly one principal checkpoint/PR action using the
     AUTONOMY.md priority rule
     -> if an already-approved eligible Gate-AI PR awaits finalization:
        mechanical finalizer only; exact approval/HEAD and allowlisted-closure
        checks, then expected-HEAD protected closure and merge
     OR
     -> if semantic checkpoint work is eligible:
        adapter/semantic worker only; structured result and publication as
        authorized by that checkpoint
  -> host: record non-sensitive operational evidence and release exclusion
```

Each invocation ends as a completed authorized transition, `NO_OP`, a durable
checkpoint `BLOCKED` supported by objective evidence, runtime failure, or an
interrupted/untrustworthy execution. Only the consumer policy can support the
checkpoint-state transition; operational failures remain operational.

A semantic-worker invocation that publishes `AI_REVIEW` ends at publication.
AI SUPERVISOR review is a separate durable step. A later closure-and-merge
finalization is a subsequent invocation only after durable `APPROVED` exists;
it remains one operational finalization unit. An invocation must not perform
new semantic work, obtain or assume approval, and finalize that same work.

## 10. Recovery and idempotency principles

- Establish a durable, inspectable handoff before each side effect that could
  otherwise require repeating semantic work.
- On restart, reconcile fresh GitHub state, the selected repository, relevant
  branch/PR identity, substantive HEAD, and observable filesystem state before
  doing work. Do not trust a stale local recovery marker over durable state.
- Make repeat attempts converge: never force-publish, never recreate valid
  active work, and do not re-run a semantic worker when durable/observable
  evidence proves its work already reached the next mechanical step.
- Treat lock loss, cancellation, malformed structured output, unknown changed
  paths, or incomplete publication as untrustworthy runtime outcomes requiring
  reconciliation, not automatic `BLOCKED` transitions.
- Keep closure derivation independently repeatable and reject any diff outside
  the allowlist. Bind merge to the expected approved HEAD.

## 11. Multi-repository compatibility requirements

Future orchestration may choose one consumer repository and invoke this runner,
but ARCH-01 does not design that orchestration. To remain reusable, the runner
needs these architectural properties:

- repository path and intended ref are supplied invocation inputs;
- orchestration selects only the consumer repository; it need not supply
  consumer checkpoint branch names or private branch inventories;
- consumer-specific durable policy, queue, gates, decisions, and context are
  read from the selected consumer repository;
- active branch/PR identity is reconciled from that consumer's fresh durable
  state, while deterministic derivation applies only to a genuinely new
  checkpoint without a legitimate active branch/PR;
- locking, recovery, logs, credentials, remote identity, and Git/GitHub actions
  are scoped per selected repository;
- no hard-coded consumer paths, checkpoint names, product rules, or private
  repository inventory are embedded in the runner; and
- one invocation remains limited to the selected repository and one principal
  checkpoint/PR unit.

## 12. Risks and open questions

- The ordinary-Windows installation and lifecycle of a Python runtime need a
  later owner-approved packaging/provisioning decision.
- The exact structured worker-result contract, recovery-record persistence,
  lock implementation, observability sink, Git/GitHub interface, and supported
  Python/platform versions remain open; choosing them now would be premature.
- Consumer-policy parsing needs a later evidence-based compatibility strategy
  that applies the contract without re-implementing its semantics in runtime
  code.
- The security model for host credentials, and any deployment environments,
  remains a human-owned concern and must not be inferred from this assessment.
- The eventual finalizer needs focused adversarial testing of approval
  invalidation, closure allowlisting, remote races, and expected-HEAD handling.

## 13. Decisions intentionally deferred

This assessment does not adopt a language, packaging method, scheduler,
configuration or result file format, logging destination, lock primitive,
Git/GitHub library or CLI, repository layout, CI/workflow, or multi-repository
orchestrator. It does not authorize implementation, a finalizer, scheduler,
credentials, automation, or any policy change. It also does not add an entry to
`DECISIONS.md`: recommendation is not durable adoption.

## 14. Human-gate analysis

**A separate HUMAN decision is required before implementation.** The preferred
core/launcher split is a significant implementation architecture choice with
long-lived consequences for installation, maintenance, portability, privilege
boundaries, and eventual multi-repository operation. These are within the HUMAN
OWNER's material architecture authority in `AUTONOMY.md`, and resolving them
would exceed the high threshold for an AI-only assessment.

ARCH-01 should proceed to `AI_REVIEW`. If the supervisor agrees with this
assessment, ARCH-02 should be the planned Gate-HUMAN checkpoint to accept,
reject, or redirect the recommendation before any implementation checkpoint is
made ready. That human decision must be durable; this document does not make it
on the owner's behalf.

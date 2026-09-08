# Agent Instructions

<!-- GOVERNANCE:BEGIN -->
Status: normative  
Scope: organization  
Source: work-governance
Revision: 2746015fddc02636772fedb9d8672b244cea0b4e

This managed block is a self-contained projection of the common operating
contract. `work-governance` remains its canonical source.

### Mandatory baseline

- MUST NOT present unsupported evidence, specifications, formulas, APIs,
  formats, methodologies, or contracts as authoritative. Use reliable evidence,
  state uncertainty, or leave the affected work blocked. Identify any expressly
  permitted approximation as an approximation.
- MUST NOT claim an unperformed test, validation, or check, or hide a failure or
  blockage behind a fabricated implementation.
- MUST NOT destroy, overwrite, or rewrite existing work without necessary
  authority. Inspect what would be lost first.
- Technical capability is not authorization. Privileged, destructive, or
  irreversible operations require both explicit task need and sufficient
  authority. This includes force pushes, history rewrites, branch deletion, PR
  merges, repository-policy changes, and operations on production, real data,
  or external services.
- MUST NOT commit credentials, tokens, passwords, keys, secrets, or operational
  or sensitive data that should not be stored in the repository.

Projects may strengthen but MUST NOT weaken this baseline. A task defines the
objective and scope within applicable rules; it does not override the mandatory
baseline. It may satisfy only a condition or authorization that a rule
expressly contemplates.

### Common operating defaults

- Before editing, MUST identify the repository, current branch, latest commit,
  and working tree state; read all normative sources named below; and understand
  the requested scope.
- Existing work state takes priority over starting from the published baseline.
  MUST continue an identified branch or PR state rather than recreate it or
  open a replacement. Stop modifications when that state cannot be identified
  safely or when a branch or PR that is itself the operation target is
  unavailable.
- A managed environment MAY expose its provisioned checkout under a generic
  local branch name, without a local ref for the published branch or a
  configured remote. Those conditions alone MUST NOT block work or require
  creating or reconstructing a local `main`, `master`, or other
  published-branch ref. A request to start from the published baseline normally
  constrains the base state, not the local branch name. The provisioned checkout
  MAY be used when the working tree is understood and available evidence does
  not contradict that state identity; otherwise, stop and report the blockage.
- MUST keep changes focused and avoid unrelated refactors or broad cosmetic
  work. MAY make supporting changes reasonably necessary for correctness.
- MUST run relevant available checks and SHOULD broaden them when risk warrants
  it. Distinguish checks that passed, failed, or were not run, and report
  limitations or discrepancies.
- MUST keep affected documentation accurate and record only consequential
  architectural, methodological, or contractual decisions.
- SHOULD favor deterministic, repeatable, inspectable processes and MUST NOT
  treat derived results as sources of truth.
- A blockage report MUST identify the blocked requirement, missing evidence,
  checks performed, and work deliberately left unchanged.
- Before finishing, MUST review the relevant final diff and follow the
  repository's established PR, review, and merge process. MUST NOT claim a
  commit, push, PR, or merge occurred unless it actually occurred.

Explicit normative project constraints may replace these common defaults where
the mandatory baseline permits variation. Silence or omission is not an
exception. Unadopted template text is only a default.

The content between the governance markers is managed. A future synchronizer
MAY replace only content between those markers. It MUST NOT edit content outside
them or silently destroy local rules.
<!-- GOVERNANCE:END -->

## Project-specific instructions

- Read `PROJECT.md` and the normative documents in `docs/` relevant to the
  requested scope before changing this repository.
- This repository is public. Do not add consumer-private context, operational
  details, credentials, or secrets.
- Until a later approved checkpoint defines implementation architecture, do not
  add runner code, a finalizer, adapters, schedulers, workflows, or technology-
  specific operational commitments.
- The current project-specific documentation check is `git diff --check` plus
  a focused consistency and scope review. No test suite exists at bootstrap.

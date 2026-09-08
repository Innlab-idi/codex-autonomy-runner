# Project contract

## Mission

Provide a public, reusable foundation for execution mechanisms that operate
compatibly with a consumer repository's explicitly adopted autonomous-work
policy.

## Scope

This project defines the portable governance-to-execution contract, its safety
boundaries, and the future architectural questions required before any runtime
is implemented. A future implementation may provide mechanical execution
capacity for workers, adapters, hosts, and safeguarded finalization.

## Non-goals

This repository does not own a consumer's product, architecture, roadmap,
`WORK_QUEUE`, gates, local decisions, private context, or delegation of
authority. It does not make installation a grant of authority, and it does not
currently implement a runner, finalizer, scheduler, GitHub workflow, or
auto-merge facility.

## Safety and authority boundaries

`work-governance` is the common organizational baseline adopted at
`2746015fddc02636772fedb9d8672b244cea0b4e`. This repository translates no
organizational rule into runtime dependency, and it creates no circular
dependency with that baseline.

Governance defines authority and semantic policy. Execution mechanisms provide
technical capacity. Each consumer repository explicitly decides and records
what authority, if any, it delegates to an automation operating under its own
durable policy. Access, configuration, or credentials alone never expand that
delegation.

The repository is public: content must be suitable for public disclosure and
must not contain secrets, credentials, consumer-private state, production data,
or sensitive infrastructure detail.

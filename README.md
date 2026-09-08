# codex-autonomy-runner

`codex-autonomy-runner` is a public, reusable project for future execution
mechanisms that can operate within an autonomy policy explicitly adopted by a
consumer repository. It is currently a contract/bootstrap repository: no
functional runner, finalizer, adapter, scheduler, or host integration exists
yet.

The project provides **capacity, never authority**. Installing it, configuring
it, giving it technical access, or making credentials available does not
authorize an action. Authority remains in the consumer repository's durable
policy and its explicitly delegated roles.

## Governance relationship

This repository adopts the organizational baseline of
[work-governance](https://github.com/Innlab-idi/work-governance), revision
`2746015fddc02636772fedb9d8672b244cea0b4e`. That baseline is the canonical
common source for organizational rules; it is not a runtime dependency of this
project.

The intended separation is:

```text
work-governance -> common organizational authority and rules
codex-autonomy-runner -> reusable execution capacity compatible with an adopted policy
consumer repository -> product, architecture, queue, gates, local decisions, and authority
```

Consumer repositories must explicitly adopt the authority they delegate. They
retain their own product and private context; this project neither imports nor
becomes a source of those decisions.

The initial autonomy design draws conceptual and documentary provenance from
`rmolck/my-own-governance` at `9a40aaf6ce5039ceb5d127d43ccbdf4964a61398`,
especially its Gate-AI/finalizer model. It is a design reference only, not a
runtime dependency or a second organizational baseline.

## Current status

BOOT-01 establishes the public contract. ARCH-01 assessed the implementation
architecture, and ARCH-02 was resolved by the HUMAN OWNER. D-009 adopts Python
as the deterministic host/control core; an optional minimal Windows PowerShell
launcher may be used only for Windows or Task Scheduler invocation ergonomics.
No functional runner, finalizer, adapter, or scheduler is implemented yet.
The decisions D-009 leaves deferred remain deferred. CORE-01 is the first
implementation checkpoint.

Read [PROJECT.md](PROJECT.md) for scope and [docs/ROADMAP.md](docs/ROADMAP.md)
for the proposed sequence.

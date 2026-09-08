# Work queue

Operational coordination only. Requirements, architecture, and governance live in their canonical documents.

| Checkpoint | Gate | State | Scope | Dependency / next action |
| --- | --- | --- | --- | --- |
| BOOT-01 | AI | DONE | Establish the public baseline documentary and governance contract. | AI SUPERVISOR approved substantive HEAD `f70191b7d1e44af83d831f6462a877c4130dcada`; closure materialized. |
| ARCH-01 | AI | DONE | Assess implementation architecture and prototype lessons; recommend a portable runtime design without implementing it. | AI SUPERVISOR approved substantive HEAD `54f7f6b529165b58cdc2d6ee71a3cadf896b1a79`; closure materialized. |
| ARCH-02 | HUMAN | DONE | Materialize the HUMAN OWNER-approved implementation architecture in canonical documentation. | HUMAN OWNER approved the architecture in durable issue #3 and explicitly authorized closure/merge after AI SUPERVISOR review of substantive HEAD `0c4ef4d2ca333ec383471a12ce96c15dbaf07587`; closure materialized. No implementation checkpoint is READY. |
| CORE-01 | AI | READY | Implement only a deterministic Python foundation for native-process execution: structured argv and explicit cwd; explicit UTF-8 boundaries; structured exit code, stdout, and stderr capture; launch-failure distinction; unit tests for success, non-zero exit, stdout/stderr, spaced/quoted arguments, non-ASCII text, cwd, and launch failure. Excludes Git/GitHub, governance/queue parsing, worker/adapter, consumer configuration, locking, recovery, persistent observability, finalizer/merges, scheduler/launcher, multi-repo orchestration, packaging, Python/platform version decisions, and consumer-specific rules. | HUMAN OWNER explicitly authorized opening this bounded first implementation checkpoint. |

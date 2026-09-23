# CI Coordinator Developer Environment Specification

Status: active requirement package

Owner: `ci-coordinator.developer-environment`

## Purpose

This package owns reproducible tools and tasks, connected local topology,
concurrent-worktree isolation, local secret handling, lifecycle ownership, and
the production-faithful PostgreSQL principal split. Independent requirements
own Dev Container reproducibility, portable-provider isolation, runtime secret
handoff, and reversible source watch so one witness cannot stand in for another.

## Authority

```text
requirements.v1.json
  -> proofkit/requirement-bindings.json
  -> lifecycle, Compose, container, and PostgreSQL witnesses
  -> local-development implementation
```

## Non-Claims

This package does not claim production deployment parity, production secret
custody, provider availability, hosted development environments, native
Windows or Intel macOS support, automatic cleanup after a worktree root is deleted, or
developer machine security outside project-owned resources.

## Current Design Successors

- [Developer state schema evolution](../../features/developer-state-schema-evolution.md)
- [Control-plane identity authority cutover](../../features/control-plane-identity-authority-cutover.md)
- [PostgreSQL platform baseline](../../decisions/postgresql-platform-baseline.md)

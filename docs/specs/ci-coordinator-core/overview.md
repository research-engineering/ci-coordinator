# CI Coordinator Core Requirements

Status: active backend requirement package

Owner: `ci-coordinator.core`

## Purpose

This package owns the product and architecture requirements implemented by the
Python backend. Requirement-owned machine profiles and regression vectors are
the conformance authority.

## Core Law Projection

The canonical laws are MS-1 through MS-3 in the
[meta-specification](../../architecture/01-meta-specification.md). This package
projects their shortest operator-readable form:

```text
Deterministic proof may reduce work.
Agent advice may only increase validation.
Uncertainty runs FullCI or produces an explicit failure.
```

## Scope

The core requirement package covers dynamic CI planning, proof boundaries, module
decomposition, runner-capacity scheduling, shadow rollout, persistence,
operator workbench authority, and compatibility safety.

## Non-Claims

This package does not approve production rollout, dynamic omission enforcement,
live provider behavior, or UI readiness.

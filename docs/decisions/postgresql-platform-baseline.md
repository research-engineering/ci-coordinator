# PostgreSQL Platform Baseline

Status: accepted decision

Date: 2026-09-02

Owner: `ci-coordinator.developer-environment`

## 1. Decision

Use the official `postgres:18.6-trixie` multi-architecture image pinned to OCI
index digest:

```text
sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280
```

This decision supersedes only the PostgreSQL 18.4 patch-level predicates in
[the local development environment decision](local-development-environment.md).
Its topology, principal split, lifecycle, and security decisions remain
unchanged.

## 2. Rationale

PostgreSQL 18.6 is the current stable PostgreSQL 18 maintenance release. The
upstream release notes state that 18.6 contains fixes from 18.4 and requires no
dump/restore for an 18.x upgrade. Version 18.5 was not released because of a
post-wrap regression. Remaining on 18.4 therefore accepts known fixed defects
without preserving a compatibility property required by this pre-release
project.

```text
SameMajor(18.4, 18.6)
and UpstreamDeclaresNoDumpRestore(18.4 -> 18.6)
and NewerStableFixRelease(18.6)
and ExactImageDigestAvailable(18.6)
=> Prefer(18.6)
```

Evidence:

- [PostgreSQL 18.6 release notes](https://www.postgresql.org/docs/release/18.6/)
- [Official PostgreSQL container image](https://hub.docker.com/_/postgres/)

## 3. Scope

The digest is shared by Compose and Testcontainers so development and
integration witnesses select the same immutable multi-architecture image.
Product documentation and active test fixtures project PostgreSQL 18.6.

The database compatibility protocol remains major-version based. No Alembic
revision or application schema change is created for a PostgreSQL patch update.

## 4. Alternatives

| Alternative                         | Decision | Reason                                              |
|-------------------------------------|----------|-----------------------------------------------------|
| Keep 18.4                           | reject   | retains defects fixed by the current stable release |
| Use mutable `postgres:18`           | reject   | tag movement breaks exact evidence replay           |
| Pin an architecture-specific digest | reject   | breaks the admitted AMD64/ARM64 host set            |
| Adopt PostgreSQL 19 beta            | reject   | pre-release database is not a production baseline   |

## 5. Verification And Reversal

Admission requires image-index inspection, Compose contract tests, PostgreSQL
integration tests, migration upgrade/downgrade witnesses, and schema/principal
attestation. Reversal before production may repin 18.4; it must not claim that
an 18.6-initialized production cluster was rollback-tested without provider
evidence.

## 6. Non-Claims And Revision Trigger

This decision does not prove managed-provider availability, production backup,
restore, extension reindexing, or security-setting cleanup. A production
upgrade requires provider-specific release-note review and retained migration
evidence. A newer stable PostgreSQL 18 maintenance release triggers the same
bounded review; a major-version change requires a new decision.

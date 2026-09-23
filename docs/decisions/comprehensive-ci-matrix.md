# Comprehensive CI Matrix

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: accepted implementation contract

Date: 2026-09-20

## Problem And Scope

The repository already has native language, database, browser, mutation,
workflow, release and provider checks. Dockerfile analysis, independent YAML
admission, portable vendor schemas, Go static and vulnerability analysis, and
spelling need explicit execution owners. The native witness catalog and the
coordinator's target execution catalog are different contracts; enumerating one
does not make the other executable or production-admitted.

The first consumer is this repository. Coverage is relative to its tracked
input universe, owner requirements and admitted check contracts. It is not a
claim that all defects, all possible tools or all semantic properties are
mechanically decidable.

## Decision

Keep `proofkit/witness-plan-input.json` as the command authority. The new
`proofkit/ci-matrix.v1.json` assigns every declared command a disposition and
every observed tracked path at least one check surface. A new unsupported file
kind, missing command, duplicate assignment or unbound utility fails admission.
The matrix is descriptive of proof scope; path accounting does not close a
semantic requirement or authorize omission.

Add nine fixed utility commands in five execution groups:

| Group              | Commands                      | Reason                                                     |
|--------------------|-------------------------------|------------------------------------------------------------|
| Configuration      | yamllint, check-jsonschema    | Shared locked parsers and overlapping configuration inputs |
| Docker             | Hadolint, Docker Build checks | Shared Dockerfile/context inventory and build provider     |
| Go static          | gofmt, go vet, Staticcheck    | Shared compiler, module and source closure                 |
| Go vulnerabilities | govulncheck                   | Independent time-sensitive advisory database               |
| Spelling           | Codespell                     | Documentation and fixture-specific dictionary policy       |

Group execution admits the independently declared command set, retains each
terminal result and fails on nonzero status, process error, incomplete output
or input drift. Missing, duplicate and foreign result rows cannot produce a
pass. A receipt identifies the source commit, input digest, run/attempt and
command identities. It is execution evidence, not a signed production receipt.

Native Python, frontend, ShellCheck, actionlint, zizmor, PostgreSQL, API,
browser, mutation, diagrams and runtime checks remain with their existing
owners. GitHub CodeQL/secret scanning and release Grype/provenance retain
separate authority; an unchanged source diff does not establish the freshness
of a vulnerability database or provider configuration.

Branch-head command closure includes the disjoint provider utility groups
declared by this matrix. Their locked utility environment, Go tools and Docker
builder are prepared by their native CI jobs. The exhaustive witness compares
the complete command catalog with branch-head commands, admitted utility-group
commands and the existing explicitly subsumed commands; it also admits every
utility execution and its independent required-gate assertion. It cannot treat
an unbound or ungated utility as a branch-head exception or assume its tools
exist in the backend environment.

New utility schemas use pinned bundled or immutable local inputs. Unsupported
vendor extensions stay under the native vendor witness rather than being
replaced with a permissive schema. Intentional fixture text and immutable
upstream resources may have exact, reasoned exceptions; a blanket directory
skip or suppression of unrelated future diagnostics is not an acceptable
substitute.

## Coordinator Integration

Generate a self-consumer projection from the current native workflow job bodies
and matrix. Keep source, generated workflow, registry, catalog, test manifest,
dependency graph and control bundle in a checked parity relation. The generated
workflow exercises actual native jobs, not placeholder commands.

The three native Python collection/shard/combination jobs are one semantic
coverage obligation with three required witnesses. The requester entrypoint
and its `plan_url_invalid` assertion are likewise one contract; a generic
successful-job result cannot replace that assertion.

The initial self-consumer is a qualification route. Native Full Check remains
independent and required. Missing trust material or an unavailable/invalid plan
runs the full declared job set. Native-family omission remains disabled until
its complete responsibility relation and external production admission are
qualified under the existing production-authority owner. Registration or this
PR does not activate authority.

The qualification catalog permits selective omission only for configuration
and Go static groups, whose complete direct input sets are admitted from the
matrix and actual module inventory. Unknown paths, inventory changes and
changes to control or dependency owners select full CI. Advisory-driven Go
vulnerability checks, Docker and spelling remain mandatory, alongside native
families. This deliberately narrow first omission boundary must expand only
after a complete responsibility relation and independent falsifiers exist for
the additional family.

## Dependency And Compatibility Policy

Resolve direct dependencies and CI tools to stable releases available at the
observed instant on 2026-09-20; pin distributions and release hashes where the
owner supports them. Verify official documentation for configuration, output,
failure semantics and compatibility. A pinned upstream stable tool may itself
require a prerelease transitive dependency; preserve and disclose that fact
rather than claiming an entirely stable transitive graph.

Python and Node runtime upgrades preserve the repository's required behavior,
coverage floors, resource budgets and artifact contracts. Tool upgrades do not
permit silently relaxing a rule to obtain a green result. The formatter may
retain an older syntax floor for Python resources executed by external
consumers; the actual runtime and typechecking authority remain explicit.

This decision replaces the older exact runtime bindings with Python
3.14.7 and Node 26.9.0. The module-ownership v2 signal grammar still parses the
Python 3.13 language subset explicitly; its admitted interpreter is now
CPython 3.14.7. Unsupported newer syntax remains unknown and selects review.
The formatter also retains that syntax floor. Earlier design documents remain
unchanged historical rationale; their older runtime numbers are not the
current installation authority.

Python 3.14's cancelled `asyncio.shield` can report a retained producer failure
before the Keycloak cache consumes it. For those two caches, `asyncio.wait`
followed by `Task.result` preserves waiter-cancellation isolation while leaving
failure retrieval with the cache owner. This supersedes only the wait primitive
in the [refresh failure budget design](../features/keycloak-refresh-failure-budget.md);
the producer-owned clock, retry interval, task-identity guards and close contract
remain unchanged. The HTTP measurement reporter also explicitly closes rejected
file-like HTTP responses before propagating failure.

The locked dependency installer uses uv's
[`--compile-bytecode`](https://docs.astral.sh/uv/reference/cli/#uv-pip-sync--compile-bytecode)
only during `pip sync`, before installing editable repository source. The
hypothesis is that one-time dependency compilation reduces repeated import
preparation without caching mutable source. Former private diagnostic timings
and receipts are removed. Qualify the tradeoff at the new exact source, including
one-time preparation cost and each native mutation baseline.
Keep plugin discovery, mutant-source `PYTHONDONTWRITEBYTECODE=1`, the 5000ms
deadline and exit/report admission intact. Source-cache changes, identity drift
or continued timeouts reopen this preparation decision.

Debug startup also observes the pinned debugpy 1.8.22 native
`DEBUGPY_ADAPTER_ENDPOINTS` contract before publishing its attach endpoint.
The adapter writes that JSON line after binding its client/server listeners;
Docker's published port alone does not prove the internal listener exists.
Each transition supplies a unique container-local filename, reads only bounded
closed endpoint data under the existing 30-second privilege/startup deadline,
and checks the exact container/start identity before and after the observation.
This uses the existing debugpy CLI without a probe connection, new launcher or
kernel socket-table parser. The later 45-second DAP witness, mapped breakpoint,
continue/disconnect responses and ordinary restoration remain required. A
valid endpoint file cannot prove the adapter will remain live; subsequent EOF
is still fatal. Requalify this native file contract whenever debugpy changes.

The development Vite watcher uses the documented
[`awaitWriteFinish: true`](https://github.com/paulmillr/chokidar/tree/3.6.0#performance)
option through [`server.watch`](https://vite.dev/config/server-options#server-watch).
A synthetic regression must reject consecutive writes that update disk while
leaving the browser on a previous module value. Former private A/B measurements
are not exported as qualification. Native write stabilization trades a bounded
stability interval for completed-write delivery; measure feedback latency under
the pinned implementation before claiming an improvement.
The browser lane must load the actual development config and check creation,
two successive module responses and renders in the same document, and removal.
Keep the Compose HMR witness and its 70/10/90-second work/cleanup/process
budgets unchanged. Requalify on watcher or feedback-latency contract changes.

Source projection must also prove that Vite initialized the controlled probe's
HMR context before browser navigation. Raw JavaScript, HTTP success and rendered
text alone do not prove that initialization. The synthetic counterexample must
distinguish static-file fallback or cached missing-file resolution from an
admitted transformed module; no former private bootstrap receipt is inherited.
The source-projection poll requires the pinned Vite initialization prefix for
the exact probe path and its initial marker assignment, rather than raw text
containing `created`. Unknown formats fail closed and require version
requalification. Preserve Vite's cache, configuration and retry policy and the
later behavioral/document/socket checks.

## Alternatives And Revision

MegaLinter remains useful for comparative discovery. Making it a second
mandatory scheduler would duplicate command ownership and configuration
discovery without demonstrated unique coverage. Fixed native adapters reuse
existing process bounds and leave requirement, result and execution authority
with current owners.

Separate jobs for every utility repeat setup for identical input/runtime
surfaces. One monolithic job prevents independent scheduling of unrelated
inputs and advisory freshness. The five groups balance those costs without
claiming a global optimum. Revisit grouping using measured runner time,
critical-path latency, unique findings and triage cost.

This decision does not add a general shell executor, cross-run evidence reuse,
new signing authority or an automatic waiver mechanism. Revisit an adapter
when its version, native output, schema, input universe or execution contract
changes. A failing compatibility corpus requires repair or explicit owner
resolution, not an inherited green status.

## Validation

Positive, isolated-negative and boundary witnesses cover unknown file kinds,
empty/duplicate inputs, missing and foreign command results, native diagnostic
parsing, exception scope and stale generated artifacts. Run behavioral
witnesses in the existing GitHub route. Local static success does not replace
exact-subject native CI, provider or deployment qualification.

Related owners: [module ownership](../architecture/cross-cutting/module-ownership-and-decomposition.md),
[production admission](../architecture/cross-cutting/production-admission.md),
[Proofkit adoption](../architecture/cross-cutting/proofkit-adoption.md).

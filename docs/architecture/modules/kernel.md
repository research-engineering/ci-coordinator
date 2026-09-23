# Kernel Module Specification

Status: module specification

Date: 2026-07-15

## 1. Owned Invariant

Canonical representation is stable, deterministic, and shared by every module
that hashes, signs, compares, or replays decisions.

## 2. Public API

```text
canonical_json(value, resource_limits=None) -> bytes
try_canonical_json(value, resource_limits=None) -> ResultValue[bytes]
is_safe_json_integer(value) -> bool
load_strict_json(raw_bytes, max_bytes=None) -> object
StrictJsonError
hash_object(value) -> Sha256Digest
try_hash_object(value) -> ResultValue[Sha256Digest]
utf16_sort_key(value) -> bytes
ResultValue[T] = Ok[T] | Err
Clock.now() -> Instant
```

## 3. Private Boundary

The module owns canonical JSON rules, strict untrusted JSON-byte admission,
UTF-16 ordering keys, typed result values, and clocks. It must
not own product policy, GitHub semantics, persistence, or cryptographic key
management.

## 4. Input Completeness Rules

Let `M = 2^53 - 1`. The Python kernel number domain is:

```text
AdmissibleJsonNumber(n) iff finite(n) and abs(n) <= M
```

This value-domain rule applies equally to Python `int` and Python `float`; a
host-language representation cannot widen it. Negative zero is admitted but
normalizes to positive zero. Number text
uses the ECMAScript shortest round-tripping representation within the admitted
domain.

`canonical_json` accepts only exact JSON-domain builtins: null, booleans,
admissible numbers, Unicode scalar strings, arrays, and string-keyed objects
whose keys are Unicode scalar strings. It rejects non-finite or out-of-domain
numbers, unpaired surrogates, bytes, datetime objects, sets, object instances,
primitive or container subclasses, and implicit serialization hooks.

The kernel owns one parameterized structural resource measure. An owning wire
or persistence schema supplies a versioned `JsonResourceLimits`; the kernel does
not silently impose product compatibility policy on unrelated identity or OIDC
callers. The normative audit limits and failure shape are owned by
`docs/specs/ci-coordinator-core/audit-json-resource-profile.v1.json`. For a
profile `P`, the generic measure is:

```text
MAX_JSON_DEPTH = P.maxDepth
MAX_JSON_NODES = P.maxNodes

Depth(root) = 0
Depth(child) = Depth(parent) + 1

Nodes(scalar) = 1
Nodes(array(x_1, ..., x_n)) = 1 + sum(Nodes(x_i))
Nodes(object(k_1: x_1, ..., k_n: x_n)) = 1 + sum(Nodes(x_i))

AdmissibleJsonResources(x) iff:
  maximum visited Depth <= MAX_JSON_DEPTH
  and Nodes(x) <= MAX_JSON_NODES
```

Object keys label edges and are not nodes. Empty arrays and objects each count
as one node. Repeated acyclic references count once per serialized occurrence;
only references already active on the current traversal path are cycles.

Admission uses deterministic pre-order traversal. A visit checks depth first,
then increments and checks the node count, then applies scalar or container
validation. Arrays visit children by ascending index. Objects visit values by
Unicode scalar key order. Consequently the same admitted JSON tree has the same
resource result independent of object insertion order.

After recognizing an exact array or object host container, the traversal applies
the profile-owned candidate-slot guard: declared array length or object own-key
count is compared with the remaining node budget. For a canonical JSON
container, every slot contributes at least one node, so overflow is inevitable.
For a malformed container, the guard is a protective pre-validation precedence,
not a claim that sparse positions, symbols, accessors, or non-enumerable
properties are JSON nodes. It returns `json_max_nodes_exceeded` at the container
pointer with `observed = MAX_JSON_NODES + 1` before descriptor expansion, key
sorting, descendant-shape validation, or child validation. Therefore candidate
slot exhaustion also precedes an invalid scalar or excess depth hidden below the
container.

Both runtimes use an explicit traversal stack. Therefore every non-negative
`max_depth` accepted by the parameterized Python kernel terminates with either
canonical bytes or a typed domain failure; host recursion depth is not part of
the public result.

Depth and node exhaustion are typed deterministic domain failures with stable
codes `json_max_depth_exceeded` and `json_max_nodes_exceeded`; no exhausted value
may be hashed, signed, or persisted by the owning audit boundary. The profile,
rather than the kernel, declares whether limits are runtime tuning knobs. The
audit-v1 profile forbids runtime tuning; changing either limit for an existing
schema version is a domain change governed by `REQ-CI-CORE-010`. An omitted
resource profile preserves the wider existing kernel domain and is not
admissible for audit-v1 persistence.

Canonical admission is closed under its detached JSON snapshot:

```text
Admit(x) => Admit(Snapshot(x))
         and canonical_json(Snapshot(x)) = canonical_json(x)
```

`hash_object` must hash only the bytes produced by `canonical_json`. It must not
accept pre-hashed values as semantic inputs.

`load_strict_json` is an ingress parser, not a canonicalizer. It accepts only
exact optionally byte-bounded UTF-8 `bytes`; rejects duplicate object keys,
non-finite constants, finite-float overflow, unpaired surrogate keys or values,
invalid encodings, and parser recursion; and returns a detached JSON tree.
Integer range and product-specific shape or resource policy remain caller-owned,
so parsing cannot silently widen canonical or wire admission.

`Clock` must be injected at boundaries that need time. Domain modules cannot
read wall-clock time through the kernel without an explicit `Clock` dependency.

## 5. Fallback Behavior

```text
NonCanonicalInput => typed Result error before hashing or signing
UnknownSerialization => typed Result error before durable persistence
JsonDepthExhausted => json_max_depth_exceeded before hashing or signing
JsonNodesExhausted => json_max_nodes_exceeded before hashing or signing
ClockUnavailable => typed Result error at the caller boundary
MalformedStrictJson => StrictJsonError before provider data reaches a domain decoder
```

The kernel never chooses FullCI. It returns typed deterministic failures so the
owning caller can apply its fallback law.

## 6. Audit And Replay Facts

Canonical bytes and hashes are replay inputs. Replaying the same semantic value
must reproduce identical bytes and digests independent of dictionary insertion
order, process locale, timezone, or Python runtime hash randomization.

Kernel errors must include stable reason codes; free-form exception text is not
a replay fact.

## 7. Forbidden Imports

```text
fastapi
sqlalchemy
github clients
http clients
environment settings
planner modules
```

## 8. Proof Obligations

| Obligation                                                    | Falsifier                                                                                                                                      |
|---------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| Same semantic object produces same canonical bytes.           | Reordered object keys change hash.                                                                                                             |
| Semantic differences alter hashes.                            | Changed nested value keeps same digest.                                                                                                        |
| Canonical admission is snapshot-closed.                       | Canonical bytes parse into a value rejected by the kernel.                                                                                     |
| Strings contain Unicode scalar values only.                   | An unpaired surrogate reaches hashing.                                                                                                         |
| Host-language subclasses cannot widen JSON admission.         | An `IntEnum`, `int` subclass, or `float` subclass is admitted.                                                                                 |
| Structural resources are bounded identically across runtimes. | A value at depth 65 or node 10,001 reaches hashing in either runtime.                                                                          |
| Resource accounting is a tree measure.                        | An empty container counts as zero, an object key counts as a node, or an alias is counted only once.                                           |
| Resource failure is deterministic.                            | Object insertion order changes the stable resource error code.                                                                                 |
| Resource admission is host-stack independent.                 | An admitted parameterized depth produces `RecursionError`, `RangeError`, or process termination instead of canonical bytes or a typed failure. |
| Candidate-slot exhaustion is rejected before child work.      | A container over the declared slot budget materializes all descriptors, sorts all keys, or validates an invalid tail scalar first.             |
| Untrusted JSON bytes have one strict parser contract.         | A duplicate key, NaN, float overflow, invalid UTF-8, or unpaired surrogate reaches a provider decoder.                                         |
| Clock use is injected.                                        | Pure domain module reads wall clock directly.                                                                                                  |
| Result types preserve failure codes.                          | Error path raises untyped exception across context boundary.                                                                                   |

## 9. Implementation Mapping

Target files:

```text
ci_coordinator/kernel/canonical_json.py
ci_coordinator/audit_replay/json_resources.py
ci_coordinator/kernel/hashing.py
ci_coordinator/kernel/clock.py
ci_coordinator/kernel/result.py
ci_coordinator/kernel/ordering.py
ci_coordinator/kernel/strict_json.py
```

## 10. Acceptance Tests

- golden canonical JSON fixtures.
- property tests for ordering invariance.
- scalar-boundary and canonical snapshot-closure tests.
- exact depth 64/65 and node 10,000/10,001 boundary tests.
- empty-container, mixed-nesting, key-order, alias, and cycle accounting tests.
- deterministic requirement-owned IEEE-754 formatting corpus.
- mutation tests for hash input changes.
- direct import-boundary check: `kernel` imports no application or
  infrastructure modules.
- negative tests for non-finite numbers, bytes, datetime objects, sets, and
  object instances.
- parameterized strict-JSON tests shared across GitHub and JWKS decoder owners.

## 11. Resource Non-Claims

The structural budget does not bound UTF-8 bytes, string length, object-key
length, request-body bytes, host-object materialization, own-key enumeration,
ledger cardinality, or database storage. Transport and persistence owners must
define those independent limits before production ingress or durable retention.
The constants are conservative initial-release policy values; this specification
does not claim they were derived from an SLO or production workload distribution.

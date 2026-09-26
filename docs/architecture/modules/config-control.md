# Config Policy Admission Module Specification

Status: module specification

Date: 2026-07-11

## 1. Owned Invariant

One pure, total, deterministic operation converts a bounded repository policy
source with no designated secret fields into either an immutable compiled epoch
draft or one stable typed diagnostic. No partial public API may bypass that
pipeline.

This specification owns only policy admission. Epoch registration,
durable representation, activation, authorization, and pointer transitions are
owned by the separate config-epoch, persistence, and operator-control
specifications.

## 2. Scope

`PolicyDocumentV1` contains policy for exactly one repository scope. It may
describe coordination rules, dynamic-CI policy, workflow identities, global
risk paths, and producer-feasibility inputs admitted by its schema.

Its schema has no designated fields for:

```text
GitHub App private keys or webhook secrets
database URLs or credentials
server or process settings
signing keys or OIDC trust material
provider access tokens
unversioned UI overlays
```

Arbitrary policy strings are not secret-scanned and may still contain an
accidentally pasted secret; admission makes no content-classification claim.
Runtime credentials and process configuration remain composition-root inputs.
The first and only public operation is:

```text
admit_policy_document(raw_source: bytes, format: str)
  -> PolicyAdmissionResult
```

The format argument is an exact host string because the source profile owns a
typed `source.unsupported_format` failure. An admitted value is narrowed to
`PolicySourceFormat` only after source-phase validation; a non-string host value
is a programming-contract violation rather than policy input.

Success is an immutable `ValidatedEpochDraft`; failure is an immutable one-item
tuple containing `PolicyDiagnostic`. Parse, normalize, validate, feasibility,
and compile functions remain private.

`project_dynamic_ci_planning(draft)` is the narrow public projection for the
deterministic planner. It recomputes `epochHash` from the exact compiled
bytes and recomputes `epochId` from scope and all three identity hashes before
it reads `dynamicCi`. The projection retains the admitted epoch as its internal
authority and re-derives every exposed fact when a consumer calls
`assert_integrity()`. Therefore replacing checks, graph source, global-risk
paths, or any identity while retaining an older epoch is rejected before either
consumer value is constructed. It returns only typed planner facts, including
the policy-owned dependency-graph source and global-risk paths; raw JSON parsing
remains inside `config_control`. Both `PlanningPolicy` and repository
`PolicySnapshot` must invoke this assertion before copying facts, so their
identity coordinates cannot silently diverge in the production composition path.

The projection is public only from
`ci_coordinator.config_control.planning_projection`. It is intentionally not
re-exported by the `config_control` package root, whose `__all__` is the frozen
policy-admission result ABI owned by `policy-admission-result-profile.v1.json`.

## 3. Admission Pipeline

```text
source-byte admission
  -> strict UTF-8 decoding
  -> format parsing and resource preflight
  -> structural schema validation
  -> recursive default projection
  -> normalized structural validation
  -> semantic validation
  -> producer-feasibility validation
  -> canonical compilation
  -> output-byte admission
  -> immutable ValidatedEpochDraft
```

If phase `i` fails, no later phase runs. The profile selects exactly one failure:
the lowest failing phase, then its phase-specific deterministic rule. Every
input within the source-byte boundary terminates with success or a typed
failure; parser exceptions and library text never cross the public boundary.

`PolicyDiagnostic` is exactly:

```text
code
phase
ruleId
instancePointer
parameters
```

Diagnostics sort by
`(phaseOrderIndex, instancePointer, codeOrderIndex, ruleId, canonical_json(parameters))`,
where the profile owns `phaseOrder`, every `code -> phase` mapping, and every
`codeOrderIndex` through its `codes` array, plus every fixed or dynamic `ruleId`
projection. Messages are non-identity projections.
Raw source values never enter diagnostics. Duplicate-key diagnostics identify
the containing pointer without copying the key; unsupported-format diagnostics
list only admitted formats; semantic diagnostics use rule and pointer identity
without copying the rejected value.

Parse offsets are zero-based decoded Unicode-scalar indices. Diagnostic line and
column values are one-based Unicode-scalar positions; LF, CR, and CRLF each end
one line, CRLF is one terminator, and a non-BMP scalar occupies one column.
Scalar-domain and resource failures report the candidate token/event start, so
same-start failures use `diagnostics.codes` order rather than implementation
check order.

## 4. Machine Owners

| Contract                                                                            | Owner                                              |
|-------------------------------------------------------------------------------------|----------------------------------------------------|
| formats, parser restrictions, resources, defaults, diagnostics, compilation, hashes | `config-document-profile.v1.json`                  |
| input document structure                                                            | `repository-policy.schema.v1.json`                 |
| semantic predicates, pointer classes, path grammar, compatibility classifications  | `repository-policy-semantics.v1.json`              |
| compiled policy structure                                                           | `compiled-repository-policy.schema.v1.json`        |
| public result algebra, exact fields, identities, immutability, Python projection    | `policy-admission-result-profile.v1.json`          |
| finite producer variants, sentinels, measurement, and feasibility failure           | `config-producer-feasibility-profile.v1.json`      |
| canonical JSON and SHA-256 bytes                                                    | `modules/kernel.md` and `kernel.canonical-json/v1` |

The profile-owned `recursive-default-projection/v1` algorithm applies JSON
Schema default annotations explicitly and validates the projected value again.
Direct property annotations and terminal local-reference targets may supply an
absent-property default; `oneOf` and `allOf` do not manufacture one. Present or
inserted values recurse through `allOf` branches in array order. JSON Schema
validation alone never applies defaults.

The Python package uses byte-exact generated resource copies loaded through
`importlib.resources`; freshness witnesses compare each copy and digest to its
canonical owner under `docs/specs`.

## 5. Source Algebra

JSON and YAML are source encodings of one normalized JSON algebra. YAML is
restricted to YAML 1.2 JSON-compatible values. Admission rejects duplicate
keys, multiple documents, anchors, aliases, merges, custom tags, non-string
keys, non-finite numbers, unsafe integers, byte-order marks, and unpaired
surrogates.

An omitted YAML version directive selects 1.2; any explicit version other than
1.2 is invalid syntax. The empty YAML stream projects to JSON `null`. Resolved
timestamp, binary, set, and every other non-JSON standard tag use the
`parse.custom_tag_forbidden` failure. Valid UTF-16 surrogate escape pairs are
folded to one Unicode scalar before key comparison or construction; only an
unpaired surrogate is rejected. JSON `NaN` and infinity extensions use
`parse.non_finite_number`. The kernel safe-number magnitude applies equally to
integer and non-integer values; an out-of-range finite value uses
`parse.unsafe_integer`. Numeric source tokens denote their nearest IEEE-754
binary64 value, matching the admitted numeric value algebra; integer schema
classification is applied to that projected value. Safe integer tokens remain
exact. Distinct numeric source spellings remain distinct in `sourceHash` even
when they project to the same normalized number.

This integer domain also applies to all three dynamic-CI sharding counters
through producer feasibility and compilation. Integral decimal or exponent
spellings produce the same normalized and compiled bytes, catalog and policy
hashes as their integer spelling; exact source bytes, `sourceHash`, and the
source-bound `epochId` remain distinct. Non-integral, boolean, textual, and
out-of-bound values retain their existing typed diagnostics. The worker preserves
the pure admission result; HTTP validation retains its existing success, error,
and authentication contracts.

Depth, node, and forbidden graph features are enforced while consuming tokens
or events, before constructing the full host object. JSON receives a
string-aware preflight before `json.loads`; YAML receives an event preflight
before safe construction.

All collection cardinalities are schema-bounded. Under the kernel tree measure,
the exact maximum normalized document contains 28,787 nodes; the profile limit
32,768 admits the complete schema domain with 3,981 nodes of explicit slack.

## 6. Policy Semantics

The repository scope key is `(installationId, repositoryId)`. Owner and name
are retained assertions; a later provider-context stage verifies that all four
facts identify the same repository.

V1 rules define this precedence:

```text
PolicyEvent := pull_request | push | merge_group

Matches(rule, event, branch) iff
  rule.on.event = event and branch in rule.on.branches

SelectedRule(rules, event, branch) =
  the lowest array index i for which Matches(rules[i], event, branch)
```

Rule array order is therefore behavior and compilation preserves it. Rule names
are unique and branch values within one rule are unique. Overlap between rules
is legal and resolves by the explicit first-match relation; it is not silently
reclassified as a set.

An admitted epoch may have `dynamicCi = null`. That is ordinary observe-only
state, not a projection error: the dynamic planner returns no candidate and the
plan issuer retains FullCI. Policy admission and activation therefore cannot
turn a valid observe-only document into an unhandled request failure.

Provider signal names are bounded Unicode-scalar identities and are preserved
without normalization or transliteration. A lossy ASCII projection would name
a different provider check. Workflow file coordinates retain their narrower
case-key contract because they are repository paths rather than display names.

V1 rejects leading or trailing whitespace in identity, path, branch, workflow,
profile, risk-class, model, and prompt-hash values. This is an admitted
monotonic narrowing of current trimming behavior. It performs no Unicode
normalization or locale-dependent case folding. Case-insensitive identity keys
are restricted to ASCII and use the profile-owned `ascii-lower/v1` relation.
Rejecting non-ASCII in only those keys is an explicit monotonic strengthening
that avoids dependence on the different Unicode tables shipped by Node and
Python. Branch selection, glob matching, allowlist membership, and exact-value
duplicate checks remain case-sensitive; Unicode scalar values remain legal in
fields outside the named key set.

The semantics profile classifies predicates as `retained-exact` or
`monotonic-strengthening`. An additional rejection needs an explicit owner-bound
safety argument and exact diagnostic witnesses; a generic semantic failure is
not that proof. The current `python-config-policy-admission-mutants.v1.json`
fixture describes a finite inventory of code mutations and commands, not a
compatibility allowlist. Its consumers in `scripts/mutation/mutation_manifest.py`
and `mutation_suite_specs.py` validate mutation execution and CP inventory;
neither they nor runtime admission interpret `additionalRejectionAllowlist`.
No such enforcement is claimed.

### Path Admission Compatibility

Responsibility and global-risk patterns reuse
`kernel.path_patterns.validate_path_pattern`: nonempty scalar relative
paths, no NUL, at most 512 characters and the bounded glob grammar.
Config admission additionally rejects a terminal `/`; it neither translates
directory-like patterns nor requires a currently existing file.

```text
AdmitNew(D) => AdmitPrevious(D)
              and every policy path passes RuntimePathAdmission
              and no policy path ends with '/'.
AdmitNew(D) and dynamicCi enabled
  => both path-consuming projections can construct their path facts.
For retained admitted inputs: source/compiled bytes, hashes and matching unchanged.
```

This narrows the former admission language. Diagnostics retain
`semantics.invalid`, `path.valid-pattern`, exact offending pointers, empty
parameters and the existing whitespace-diagnostic precedence. Historical bytes
and hashes are never rewritten. Durable loads repeat source admission, so an
old invalid pattern now requires explicit replacement instead of silent
reinterpretation; the application takes its missing-active-candidate path.
Direct callers bypassing re-admission are not thereby given a total recovery
guarantee, and live signed fallback remains a separate qualification.

`src` can identify a file exactly; future-file patterns remain legal.
NFC and NFD remain distinct strings, consistent with
[Git tree paths](https://git-scm.com/docs/git-ls-tree#_output_format) and
[JCS string preservation](https://www.rfc-editor.org/rfc/rfc8785.html#section-3.1).
No provider read or general dead-pattern analysis belongs to pure admission.

The shared implementation moves unchanged into the existing kernel; the context
facade retains its public aliases. Config cannot depend on that facade, and
the import allowlist is not broadened. Literal JSON/YAML witnesses cover both
path fields, NUL, 512/513 characters, trailing separators, exact Unicode,
historical re-admission and both consumers. The packaged semantics resource
must match the canonical profile. Revisit on changed grammar, normalization,
directory semantics or historical-recovery requirements.

## 7. Compilation

The profile owns the complete input-to-compiled field projection. In summary:

| Compiled path                                   | Projection and order                                                  |
|-------------------------------------------------|-----------------------------------------------------------------------|
| scope, owner, name, default branch              | direct normalized fields                                              |
| rules                                           | map normalized rules in input order                                   |
| rule branches and omitted signals               | preserve input order                                                  |
| dynamic policy when absent or disabled          | `null`                                                                |
| dynamic risk classes and advice lists           | sorted unique by ECMAScript UTF-16 order                              |
| dependency graph source                         | direct normalized field                                               |
| global risk paths                               | sorted unique by ECMAScript UTF-16 order                              |
| obligations, witnesses, execution profiles      | map all fields, sort set-valued fields, then sort by nominal identity |
| `policyHash`, `configuredValidationCatalogHash` | exact machine-profile projections                                     |

The machine profile expands both compatibility-hash input objects field by
field. They hash canonical JSON directly without a domain separator, as defined
by the requirement-owned compatibility profile. `configuredValidationCatalogHash`
seals only the closed obligation, witness, and execution-profile catalog.
`policyHash` seals that digest plus every non-catalog planning fact, including
dependency-graph authority, global risk paths, advice policy, and fallback timeout.
Neither digest represents or proves a provider workflow inventory.

The complete result, including `dependencyGraphSource`, validates against the
compiled schema before hashing. Omitting or inventing a compiled field is not a
valid implementation.

## 8. Identities

```text
sourceHash =
  sha256("ci-policy-source/v1\0" || utf8(format) || "\0" || exact source bytes)

documentHash =
  sha256("ci-policy-document/v1\0" || kernel.canonical_json(normalized document))

epochHash =
  sha256("ci-compiled-repository-policy/v1\0" ||
         kernel.canonical_json(compiled policy))

scopeBytes = kernel.canonical_json({installationId, repositoryId})

epochId =
  sha256("ci-config-epoch/v1\0" || scopeBytes || "\0" || sourceHash || "\0" ||
         documentHash || "\0" || epochHash)
```

All digests are lowercase 64-character hexadecimal SHA-256 values. Hash text in
the `epochId` projection is lowercase ASCII, not decoded digest bytes.

Consequences:

- source formatting or source format may change `sourceHash` while preserving
  `documentHash` and `epochHash`;
- reordering a set-like input may change `documentHash` while preserving
  `epochHash`;
- changing behavior must change canonical compiled bytes and `epochHash`;
- map insertion order, locale, environment, clock, and mutable provider state
  cannot affect any identity.

`ValidatedEpochDraft` has the exact machine-owned field algebra in
`policy-admission-result-profile.v1.json`: source format and exact bytes, scope,
canonical normalized and compiled bytes, contract identities, and content
identities. Every collection is transitively immutable. Its output bytes
satisfy the profile limits before success returns.

## 9. Producer Feasibility

For the current dynamic-CI producer:

```text
P = audit-json-resource-profile.v1.json
Bp = audit-persistence-byte-profile.v1.json
ConfigVariants(c) = every reachable selected, omitted, mixed, fallback,
  advice, fixture-union, and context-present branch shape
MinExternalEnvelope(v) = v with fixed-width identities preserved and every
  externally owned variable string replaced by its valid minimum sentinel
MaxConfigFloorBytes(c) =
  max(length(canonical_json_utf8(MinExternalEnvelope(v)))
      for v in ConfigVariants(c))

NecessaryAdmission(c) requires:
  MaxProducerNodes(c) <= P.maxNodes
  and MaxConfigFloorBytes(c) <= Bp.maxPayloadCanonicalBytes
```

The profile owners supply constants; executable projection witnesses own the
source-to-runtime relation. Exceeding the floor proves rejection is necessary.
Passing it is not sufficient because external runtime strings remain unbounded
by repository policy. Runtime audit admission remains mandatory.

## 10. Proof Obligations

| Obligation                                                 | Falsifier                                                                      |
|------------------------------------------------------------|--------------------------------------------------------------------------------|
| Same normalized document has one identity.                 | JSON/YAML format or object-key order changes `documentHash`.                   |
| Source provenance includes parser mode.                    | Same bytes under different formats share `sourceHash`.                         |
| Defaults are deterministic.                                | Omitted and explicit defaults produce different normalized bytes.              |
| Current rule precedence is retained.                       | Two matching rules select other than the lowest input index.                   |
| Compilation is complete.                                   | `dependencyGraph.source` changes without changing `epochHash`.                 |
| Set order is semantic only where declared.                 | Dynamic set permutation changes `epochHash`.                                   |
| Invalid input cannot bypass a phase.                       | Compilation runs after structural rejection.                                   |
| Designated credential fields cannot become policy history. | The structural schema admits a private-key or provider-token field.            |
| Resource admission protects allocation.                    | Host recursion or object construction occurs before exhausted preflight fails. |
| Feasibility is not overclaimed.                            | Admission claims every future runtime audit event must fit.                    |

## 11. Implementation Mapping

```text
ci_coordinator/config_control/__init__.py
ci_coordinator/config_control/contracts.py
ci_coordinator/config_control/_document.py
ci_coordinator/config_control/_json_document.py
ci_coordinator/config_control/_yaml_document.py
ci_coordinator/config_control/_parser_support.py
ci_coordinator/config_control/_schema_validation.py
ci_coordinator/config_control/_dynamic_ci_compatibility.py
ci_coordinator/config_control/_rules.py
ci_coordinator/config_control/_semantics.py
ci_coordinator/config_control/_feasibility.py
ci_coordinator/config_control/_compiler.py
ci_coordinator/config_control/_admission.py
ci_coordinator/config_control/planning_projection.py
ci_coordinator/config_control/_resources.py
ci_coordinator/config_control/resources/*.json
```

`_document.py` owns only source bytes, decoding, format selection, and parser
routing. `_schema_validation.py` owns the deterministic Draft 2020-12 traversal
and keyword-diagnostic projection shared by input and compiled validation.
`_dynamic_ci_compatibility.py` owns admitted dynamic-set projection, compiled
dynamic-check projection, and the contract-defined workflow and policy hash
objects shared by feasibility, compilation, and conformance. It uses the
kernel-owned ECMAScript UTF-16 ordering primitive rather than defining another
ordering law.
`_rules.py` owns input structural admission, recursive default projection,
normalized validation, and required normalized pointers. `_semantics.py` owns
only predicates and diagnostics from the semantics profile. `_feasibility.py`
owns only the finite producer-recipe projection, resource measurement, and
feasibility diagnostic from the producer-feasibility profile. Parser support
contains no normalized-policy DTO. `planning_projection.py` owns only the
verified compiled-epoch to typed-planning projection; it neither parses source
policy nor imports planning, provider, persistence, or runtime owners.

The package imports no application, API, persistence, SQLAlchemy, GitHub,
environment, or clock owner.

## 12. Acceptance

- JSON/YAML, defaults, Unicode scalar, duplicate-key, and resource boundaries;
- exact 28,787-node schema maximum plus 32,768/32,769 preflight boundaries;
- exact admitted V1 semantics, first-match rule behavior, defaults,
  `policyHash`, and `configuredValidationCatalogHash`;
- reviewed monotonic rejection triples only;
- mutation of every compiled field/set member, default, hash projection, phase
  boundary, and immutable return path;
- independently pinned case inventory and Python-reject-all falsifier.

## 13. Non-Claims

This specification does not persist or activate an epoch, define a database
codec, expose HTTP or UI, authorize a principal, read a live GitHub catalog,
prove provider execution, approve dynamic omission, or prove native policy-admission
behavior before its implementation witnesses exist. It does not
detect secrets pasted into arbitrary policy strings; transport or repository
policy may add a separately owned content scanner before admission.

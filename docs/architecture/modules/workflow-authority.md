# Workflow Authority

Status: as-built dormant evidence capability

Implemented requirement: `REQ-CI-RUNTIME-032`

## 1. Responsibility

`workflow_authority` converts bounded retained Git object evidence into two
different content-addressed values:

1. a stable manifest of the complete `.github/workflows` subtree; and
2. an exact source binding proving that one provider-bound commit resolves to
   that manifest.

It owns neither GitHub transport nor workflow semantics. The GitHub adapter
retrieves provider bytes; workflow discovery parses regular workflow blobs;
target-authority producers combine those independent capabilities.

## 2. Values

For repository identity `Q`, retained object closure `E`, source commit `C`,
and the exact observed provider commit request `H`:

```text
W = StableWorkflowManifest(Q, E)
S = SourceBinding(Q, H, C, E.root, E.github, E.workflows, Digest(W))

WorkflowAuthorityEvidence(E, W, S) :=
  Recompute(E) = (W, S)
```

`W` contains repository identity, Git object format, workflows-subtree object
id, and every descendant path, mode, type, object id, declared size, observed
size, and blob SHA-256. `S` additionally contains the exact source commit,
provider API version, exact GET operation, path, query and absent-body evidence,
retained provider commit-response digest, and verified root-to-workflows tree
chain. `H` is retained from the successful transport outcome; reconstructing it
after the request is forbidden.

Source commit, root tree, and ancestor tree identities are absent from `W`.
Therefore:

```text
Same(Q, workflows subtree bytes and modes) -> Same(Digest(W))
Different(C or unrelated root bytes)       -> Different(Digest(S)) is allowed
Different(workflow bytes, path, mode, type) -> Different(Digest(W))
```

This distinction permits a fresh source binding for a non-CI commit without
changing stable workflow authority.

## 3. Object Closure

The capability admits SHA-1 Git objects only. It reconstructs each regular
blob and tree id from exact bytes:

```text
BlobOID(b) = SHA1("blob " || DecimalLength(b) || NUL || b)
TreeOID(t) = SHA1("tree " || DecimalLength(Serialize(t)) || NUL || Serialize(t))
```

Tree serialization follows Git ordering and mode encoding. Every retained tree
must match its declared object id. The root must contain `.github` as a tree;
that tree must contain `workflows` as a tree; every descendant tree and regular
blob must be reachable exactly once. Missing descendants, unreachable retained
objects, duplicate paths, cycles, excessive depth, symlinks, gitlinks, special
objects, incoherent mode/type pairs, size mismatch, or object-id mismatch
rejects the evidence.

The provider commit response is retained as bounded authenticated evidence and
must name the requested commit and root tree. It is not treated as raw Git
commit bytes and the generic commit object id is not recomputed.

## 4. Provider Adapter

`GitHubWorkflowAuthorityReader` performs this sequence:

```text
repository by immutable id
  -> commit at exact revision
  -> root tree
  -> .github tree
  -> complete non-recursive workflows tree traversal
  -> bounded concurrent regular-blob reads
  -> repository identity rebind
  -> pure WorkflowAuthorityEvidence construction
```

Every request is GET-only, API-version pinned, non-paginated, and checked
against its exact operation and path. Recursive Git-tree responses are not used
because provider truncation could turn omission into apparent completeness.
The successful commit request is retained verbatim as a pure bounded value and
must match the exact repository and source commit before source-binding
construction.
The repository is read again after object retrieval; rename, owner, default
branch, or identity drift rejects the result.

Provider unavailability, rate limiting, not-found, malformed bytes, missing API
version provenance, response identity mismatch, source bounds, or unsupported
objects produce a typed unavailable outcome. No failure creates partial
authority.

## 5. Bounds

The smaller applicable bound always wins. The implementation bounds:

- commit response bytes;
- tree count, depth, entries, and serialized bytes;
- manifest entries and path components;
- individual and aggregate blob bytes;
- concurrent blob retrieval; and
- canonical JSON depth, nodes, and bytes.

The pure constructors revalidate all retained evidence. Trusting adapter-local
validation alone is forbidden.

## 6. Ownership And Dependencies

| File group                                   | Sole responsibility                                          |
|----------------------------------------------|--------------------------------------------------------------|
| `model.py`                                   | Stable manifest, source binding, and retained object values. |
| `git_objects.py`                             | Exact SHA-1 Git blob/tree serialization and identity.        |
| `evidence.py`                                | Complete retained-evidence closure and recomputation.        |
| `codec.py`                                   | Exact canonical manifest and source-binding codecs.          |
| `_validation.py`, `limits.py`                | Scalar and aggregate admission bounds.                       |
| `outcomes.py`, `errors.py`                   | Closed pure and provider-facing failure values.              |
| `integrations/github/workflow_authority*.py` | GitHub retrieval and transport projection.                   |

The pure package may import only exact repository-scope and kernel primitives
plus a closed standard-library allowlist needed for value construction.
Provider, persistence, HTTP, runtime, environment, filesystem, clock,
randomness, and process mechanisms are forbidden by executable import policy.
The provider adapter may depend inward on the pure package; the reverse edge is
forbidden.

## 7. Witnesses

The witness set covers independent Git golden vectors, exact codec round trips,
non-canonical JSON, non-CI commit stability, blob/tree/size mutations, special
objects, incomplete or truncated provider trees, provider rebinding, request
ordering and retention, cross-repository request reuse, exact integer sizes,
invalid revisions, and unsafe repository identities. Import and
module-ownership gates enforce the physical boundary.

These are bounded falsifiers, not proof of GitHub availability, organization
policy, workflow behavior, test adequacy, retained production storage, or
production omission safety.

## 8. Revision

Revise this contract when the admitted Git object format, provider API evidence,
stable manifest fields, source-binding coordinate, path universe, resource
bounds, or package ownership changes. A future provider mutation does not
belong here; it activates the ambiguous remote-effect contract in
`authority-transition-safety.md`.

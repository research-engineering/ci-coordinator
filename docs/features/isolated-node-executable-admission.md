# Isolated Node Executable Admission

Status: current refinement of the consumer contract laboratory.
Owner: `consumer_contract_lab`; requirement: `REQ-CI-RUNTIME-028`.
Plan: [implementation](isolated-node-executable-admission-implementation-plan.md).

## Decision

Resolve the installed Node executable in the trusted coordinator context before
creating the target execution image. A PATH entry is a candidate, not admission:
it can identify a tool-manager shim instead of Node. Preserve the existing exact
version, same-process guard, permission model and explicit consumer environment.

Use the existing bounded subprocess primitive and a small `node_executable.py`
adapter. When the resolved candidate and host `mise` are the same file, query
`mise which node --tool core:node@24.20.0`. Do not execute the shim, auto-install,
source a shell profile or fall back to a different version after rejection.
The [mise command](https://mise.jdx.dev/cli/which.html) is read-only; its
[versioned implementation](https://github.com/jdx/mise/blob/v2026.9.0/src/cli/which.rs)
resolves an explicitly requested installed tool. A direct Node needs no mise.

## Boundary And Invariants

```text
trusted PATH candidate -> optional read-only mise resolution
  -> exact native path/version probe -> isolated target image
  -> unchanged permission/guard invocation -> synthetic lab evidence
```

1. Selected and resolved executable paths are absolute regular files outside
   the target repository. Reject relative PATH resolution and target symlinks
   before invoking either executable. Installed host tools remain trust roots.
2. Tool resolution receives only locale plus explicit HOME, XDG and mise
   directory settings. Directory settings must be absolute and outside the
   target. No credentials, Node preloads, arbitrary mise settings or target
   environment can be inherited.
   With HOME absent in a PATH-only witness, resolve the trusted host account's
   home directory explicitly. Non-default tool directories require their
   admitted environment settings or prior native PATH activation; a stripped
   setting cannot be reconstructed from a shim pathname.
3. Probe the resulting executable under the original locale-only environment.
   Require exact version and exact resolved `process.execPath`, with bounded
   output, no stderr and success. Node documents `execPath` as the resolved
   [executable pathname](https://nodejs.org/docs/latest-v24.x/api/process.html#processexecpath).
4. All admission subprocesses share one ten-second monotonic deadline.
   Existing bounded process-group cleanup remains authoritative.
5. Only the admitted absolute path crosses into target execution. HOME, PATH,
   tool-manager directories and probe data do not enter consumer controls.
6. Each control still loads the exact version guard before target code. A path
   probe is not proof against hostile same-user replacement or a malicious
   installed runtime; existing host trust assumptions remain explicit.

This successor narrows the historical runtime-composition statement about a
single environment reader: `runtime/environment.py` remains the sole production
runtime reader; `consumer_contract_lab/node_executable.py` is the sole additional
offline tool-selection reader. The import gate names that exact file; sibling
orchestration, domain and provider modules gain no environment authority.

## Alternatives And Falsifiers

Keeping LANG-only shim execution preserves the reported failure. Passing the
whole host environment to controls breaks their authority boundary. Shell
activation and a generic tool-manager framework add execution and maintenance
cost without a required consumer. The bounded mise adapter reuses the admitted
tool's query interface instead of reconstructing installation directories.

Reopen if the supported mise query cannot resolve the installed exact runtime,
if its read-only/no-install contract changes, or if measured admission cost
exceeds its deadline. Other tool managers require explicit compatible evidence;
this is not a universal shim protocol or a claim of production readiness.

Native GitHub witnesses must distinguish direct Node, a contextual shim,
malformed output, version/path drift, target-owned paths, absent tools,
deadline exhaustion and environment leakage. Keep the complete consumer,
guard, process cleanup and source-epoch witnesses; do not replace them with
mock-only equivalence or a new skip.

The existing `devcontainer.verify` witness additionally invokes
`scripts/conformance/installed_mise_node_test.py` explicitly, as the workspace
user after network disconnection and before portable proof. It exercises the
digest-pinned installed mise, a forced mise-backed Node path, and real Node
with explicit or absent HOME. This provider-specific file is outside the
default pytest testpaths; other jobs need no mise installation or test skip.
Its 60-second parent budget covers four ten-second admissions and pytest
startup. Failure rejects the parent witness while preserving container cleanup.
The existing 40-minute job budget still covers provisioning, both proof phases,
and cleanup; the portable-proof budget remains unchanged.

Oracle closure is operand-specific: the dispatcher asserts both exact roots,
the import test enumerates every current Python laboratory sibling, and all
seven admitted directory settings have independent target-owned falsifiers.
This closes the named static gaps, not arbitrary future mutations or live
provider conformance before an exact GitHub result exists.

# Explicit Outbound Proxy Implementation Plan

Status: implemented; repository verification passed; live deployment evidence
remains externally retained and is not admitted by this document

Date: 2026-07-31

Owner requirements: `REQ-CI-CORE-017`, `REQ-CI-DEV-002`,
`REQ-CI-RUNTIME-008`, `REQ-CI-RUNTIME-021`, `REQ-CI-UI-006`

## 1. Problem

The coordinator owns three HTTPS client paths that must reach GitHub from a
deployment where direct egress can be denied:

1. GitHub App API and credential exchange;
2. GitHub Actions JWKS retrieval; and
3. GitHub App OAuth code exchange.

Every client deliberately uses `trust_env=False`. That prevents ambient
`HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` variables from silently changing a
security-sensitive route, but it also means the deployment cannot reach these
dependencies through its required corporate proxy.

The required behavior is therefore not ambient proxy discovery. It is one
explicit, admitted, immutable process-wiring value supplied by the composition
root to every owned external client.

## 2. Decision

Add one optional process setting:

```text
CI_COORDINATOR_OUTBOUND_PROXY_URL
```

Its wire domain is one ASCII `http` URL with a canonical host, optional
non-default port, and either an empty path or `/`. User information, any other
path, query, fragment, default-port spelling, control characters,
non-canonical IP address, and unspecified address are rejected. Admission
normalizes the optional trailing slash away before constructing immutable
settings. `https` proxy URLs are rejected because
[HTTPX2 2.7.0 does not correctly support TLS connections to HTTPS proxies](https://httpx2.pydantic.dev/troubleshooting/);
HTTPS GitHub destinations remain end-to-end TLS over the HTTP proxy's CONNECT
tunnel.

```text
AdmittedOutboundRoute(input) :=
  input is absent
  or NormalizeCredentialFreeHttpProxyUrl(input) = canonical_url

Connected(settings) -> settings.proxy = canonical_url or absent
Disabled(settings) -> settings.proxy is absent

Normalize(http://authority/) = http://authority
Normalize(http://authority) = http://authority
Normalize(Normalize(input)) = Normalize(input)
```

The composition root passes the same admitted value to the GitHub App
transport factory, GitHub Actions JWKS provider, and GitHub browser identity
provider. Each adapter keeps `trust_env=False` and passes the value explicitly
to HTTPX. A custom injected HTTP transport and a live proxy URL are mutually
exclusive because accepting both would leave route precedence implicit.

The public settings projection exposes only whether a proxy is configured. It
does not expose internal network topology.

## 3. Proof Of Minimality

Let the hard predicates be deterministic startup, no ambient network
authority, no credential-bearing proxy URL, complete client coverage, existing
failure algebra, and backward-compatible direct mode.

| Alternative                                      | Rejection proof                                                                                                                                                                   |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Set ambient `HTTPS_PROXY` and enable `trust_env` | Process behavior depends on unadmitted ambient state and `NO_PROXY` precedence. Deterministic startup is lost.                                                                    |
| Add separate proxy settings per client           | All three destinations share one deployment egress policy today. Independent fields create invalid mixed-route states without a present requirement.                              |
| Introduce a generic HTTP client abstraction      | No shared policy beyond one scalar exists. The abstraction would own no additional invariant and would increase coupling.                                                         |
| Put proxy credentials in the URL                 | The URL would become a secret-bearing value propagated through ordinary diagnostics and HTTP client configuration. No authenticated proxy is required by the current environment. |
| Admit an `https` proxy URL                       | HTTPX2 2.7.0 documents HTTPS-proxy support as incomplete. A declared supported state would fail only at live I/O.                                                                 |
| Enable proxy only for JWKS                       | Readiness could pass while GitHub API or OAuth still fails; the connected runtime would remain operationally incomplete.                                                          |

The chosen design satisfies every hard predicate with one new scalar, direct
constructor injection, and no new domain port. Therefore it is the
minimal-sufficient boundary for the current requirement. A future requirement
for authenticated or destination-specific proxies falsifies this decision and
requires a versioned replacement contract.

## 4. Implementation Slices

### Slice A: settings contract

- admit and validate the optional canonical URL once;
- normalize the two admitted wire spellings to one no-slash internal value;
- store it on connected immutable settings only;
- reject it in disabled mode;
- project only `outbound_proxy_configured: bool`.

### Slice B: explicit adapter wiring

- pass the admitted value through runtime composition;
- configure GitHub App API, Actions JWKS, and GitHub OAuth HTTPX clients;
- retain `trust_env=False`;
- reject `transport != None and proxy != None`.

### Slice C: falsification

- parameterize valid and invalid URL boundaries;
- prove disabled and absent behavior;
- prove redaction;
- capture HTTPX construction for all three clients and assert the exact proxy
  plus `trust_env=False`;
- prove custom transport and proxy cannot coexist;
- prove runtime composition supplies the same immutable value.

### Slice D: deployment proof

- pass deployment-owned routing only as Docker predefined `HTTP_PROXY` and
  `HTTPS_PROXY` build arguments when the isolated builder also lacks direct
  egress; enable Node's environment-proxy transport only in the disposable UI
  build stage and prove the final runtime image does not inherit it; the
  repository does not admit or prove those external values;
- allow the standard local lifecycle to persist one explicitly supplied,
  admitted proxy in private per-worktree state while retaining direct mode by
  default; reject later configuration drift until the instance is reset;
- configure the remote Portainer-managed development Swarm stack with the
  corporate proxy URL; the repository-local compose topology is not the
  authority for that deployment;
- prove `/readyz` reaches ready through JWKS retrieval;
- independently prove one GitHub App or installation API read through provider
  inventory;
- independently prove OAuth code exchange followed by `/user` identity
  resolution;
- treat webhook delivery as an inbound signature/admission proof, not outbound
  proxy evidence;
- retain non-enforcing mode until separate production authority exists.

The credential-bearing provider and OAuth checks are deployment receipts. The
current local Proofkit environment declares no provider credentials, so local
Proofkit success cannot be promoted into either live claim.

## 5. Acceptance Predicate

```text
Accept iff
  settings admission is total and canonical
  and proxy normalization is idempotent
  and disabled mode rejects proxy wiring
  and public projection reveals no proxy URL
  and all three HTTP clients receive the same admitted value
  and every client retains trust_env = false
  and injected transport xor configured proxy
  and absent proxy preserves direct behavior
  and local instance state preserves or rejects one exact proxy identity
  and an isolated UI build can use only provider-supplied predefined proxy args
  and the final runtime image contains no build proxy authority
  and targeted native witnesses pass
  and Proofkit has no unknown changed-path edge
  and live JWKS, GitHub App API, and OAuth-plus-user receipts independently pass
      through the deployment-owned proxy
```

## 6. Non-Claims

This change does not prove proxy availability, proxy confidentiality, TLS
interception safety, administrator approval, authenticated-proxy support,
production readiness, provider publication, or selected-execution authority.
It does not make network routing hot-reloadable; process wiring still requires
a restart. Outbound proxy success also makes no claim about inbound public
reachability: the browser perimeter and provider-facing machine ingress remain
separate deployment authorities.

# Open-Source Portability

Status: implementation design
Date: 2026-09-23

## Purpose And Scope

Remove former deployment-specific names, addresses, account identifiers and
images from the distributable project. Replace sample data with explicitly
synthetic examples. Make deployment identity an operator-owned input, not a
source-code dependency. The product remains CI Coordinator.

The owner explicitly authorizes redaction of pre-existing documentation,
including historical design and plan payloads, for this export. This exception
permits removal of identifying context, not fabrication of replacement live
evidence. Published history and provider resources are outside this source
change and require a separate publication decision.

## Protected Behavior

- Runtime startup still requires complete authentication configuration.
- OIDC tokens and discovery must match the exact configured HTTPS issuer;
  configuration flexibility must not become token-controlled issuer discovery.
- Release evidence must bind the exact admitted repository, workflow, image,
  commit and digest. Removing a former publisher must not admit arbitrary ones.
- Sample identities confer no deployment or provider authority.
- Numeric hash substrings are not organization references. Checksums, upstream
  source attribution and third-party licenses must not be falsified.
- No corporate server, database, App, secret, installation or repository is
  contacted or modified by this cleanup.

## Minimum Sufficient Changes

Use neutral reserved domains and synthetic identities for fixtures. Admit a
deployment-configured HTTPS issuer and GitHub OAuth client identifier while
retaining exact runtime equality, role, audience, PKCE and session checks.
Use one source-owned release identity configuration shared by existing
publication and admission boundaries, rather than loose regular expressions
or identity derived from an untrusted scan result.

Replace the catalog screenshot by capturing the actual UI with sanitized demo
data. Remove identifying links and obsolete external acceptance claims from
documentation; preserve the underlying requirements and unresolved work.

The alternative of string replacement alone is insufficient: a neutral but
hard-coded identity would still prevent independent deployment. Broad wildcard
trust is rejected because portability does not justify weaker admission.

## Acceptance And Revision Conditions

Inventory all tracked text, file paths and binary assets. Verify no identifying
company references remain in the export; visually inspect the sole published
screenshot and distinguish accidental hexadecimal substrings. Rebuild native
generated projections, rebind source hashes, and run affected static and
targeted behavioral checks. Review authentication and release-identity changes
independently before publication.

Git history, pull requests, Actions artifacts, packages, caches and existing
deployments are not erased by a source commit. A public release remains gated
on the owner's final repository/history choice and a fresh provider identity
admission. No old operational receipt proves the new deployment.

## Qualification Record (2026-10-11)

This CI-064 source record is bound to base
`a1966f616e370b8f4cd64190f26afb97d649c940` and the commit that adds it. It
qualifies no provider resource, published history or release artifact.

Point-in-time inventory of the Git-observed tree (tracked plus untracked,
non-ignored files):

- No absolute workstation home path, private-materials directory, external
  report file name, review register or known retained consumer name occurs.
  The only macOS home-path shape is the synthetic cache placeholder in the
  exact upstream Grype database-status fixture; no commit reachable from the
  local refs adds another one.
- Contact addresses are reserved-domain fixtures or the upstream Lato
  attribution. Hosts are public provider, standard or upstream endpoints,
  loopback, or reserved/example domains. GitHub owners are this project, its
  Proofkit dependency, upstream tools or synthetic fixtures; numeric provider
  identities in fixtures are synthetic.
- Binary assets are the four attributed Lato WOFF2 files and the README
  screenshot. Visual inspection found only synthetic data (`example-org`,
  repeated-digit hashes and the owner-selected synthetic identity); the PNG has
  no text or EXIF metadata chunks.

Absence is guarded continuously only for one generic shape. The protected
Gitleaks policy adds the `workstation-home-path` rule for macOS `/Users` and
Windows `C:\Users` home paths with a user segment, in tree scans and in
required PR/merge-group history ranges. The reported value ends at the user
segment, so the default Gitleaks path-shaped allowance does not cover it.
Linux home paths are deliberately excluded because container and runner homes
are legitimate source. The single exception binds the exact placeholder value,
rule and owner path, like the existing fixture exceptions. Native positive,
negative, foreign-owner, changed-value and history witnesses live in
`scripts/tests/test_secret_scan.py` and run only in GitHub.

The rule is not a general private-content detector. It does not detect names,
consumer or organization identifiers, internal hostnames or screenshots, and
the default Gitleaks path exclusions (for example lockfiles, `node_modules`,
installed distribution metadata and images) still apply. Private identifiers
are never published as markers; adding one would disclose it.

Licensing is a provisional owner decision (2026-10-11): no dependency license
allow/deny policy is adopted until the owner names a legal decision owner.
Obligations are carried by the retained upstream licenses, the release SPDX
SBOM and `NOTICE`, which now names the third-party contents of built images.
No witness shows that the image SBOM enumerates JavaScript packages compiled
into the operator-UI bundle, which carries no package manifests, or that their
license notices survive minification. These remain open CI-064 work.

[SECURITY.md](../../SECURITY.md) states the telemetry and retention boundary.
It rests on the current outbound client inventory: GitHub App/API, GitHub
OAuth reviewer attestation, the GitHub Actions key set, the configured Keycloak
issuer and an optional proxy. No analytics or crash-reporting dependency
exists, and the browser contacts no third-party origin except top-level
navigation to the configured Keycloak issuer for sign-in and sign-out, GitHub
OAuth navigation and documentation links.

Provider facts need administrator evidence and stay open here: secret
scanning and push protection state, private vulnerability reporting, Actions
log/artifact/cache visibility and retention, package visibility, and the
content of published history and pull requests. CI-070 owns the release
decision that consumes them.

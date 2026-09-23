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

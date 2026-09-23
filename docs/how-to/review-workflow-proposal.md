# Review And Activate A Workflow Proposal

Status: supported administrator procedure

Last verified: 2026-09-02

## Outcome

Use a Keycloak administrator session to inspect a current-head proposal, prove
repository-manager authority through a one-use GitHub step-up, retain the exact
review, and explicitly activate the reviewed epoch.

```text
KeycloakAdministration != RepositoryOwnership
Review != Activation
Activation = CurrentReview + CurrentBaseline + FreshPermissionRecheck
```

## Preconditions

- CI Coordinator runs in a connected mode with the complete control-plane
  identity block.
- The Keycloak browser client has the exact callback and logout URLs from the
  control-plane profile.
- Your Keycloak principal has `read`, `configure`, and `activate` roles.
- The organization GitHub App is installed for the repository with the current
  read permission profile.
- The GitHub App registers exactly
  `<PUBLIC_ORIGIN>/api/v1/repository-attestations/github/callback` as a user
  authorization callback.
- Your GitHub identity currently has `maintain` or `admin` permission on the
  repository.

Provider and identity configuration is deployment-owned. Follow
[Deploy The Container Artifact](deploy-container.md) for secret, migration,
ACL, and startup order.

## 1. Sign In

1. Open `<PUBLIC_ORIGIN>/workbench`.
2. Select **Sign in**.
3. Complete the administrator login flow.
4. Confirm that the session projection shows the expected identity and roles.

The browser receives only an opaque HttpOnly session cookie and a session-bound
CSRF proof. Keycloak tokens and provider credentials are discarded or retained
only inside their exact server-side operation; none enters browser storage.

## 2. Inspect The Proposal

1. Select an admitted GitHub App installation.
2. Select an authorized repository.
3. Select **Workflows** in the repository sidebar and run discovery at the
   current default-branch head.
4. Inspect the exact provider revision, workflow graph, proven facts, unknowns,
   generated policy, proposal manifest, target epoch, and active baseline.

The review control appears only for a complete, locally re-admitted,
current-head proposal. Historical revisions, incomplete graphs, ambiguous
topology, provider failures, and manifest mismatches remain non-reviewable.

## 3. Verify Repository Authority

1. Select **Verify authority**.
2. GitHub opens a proposal-bound authorization request.
3. Select the GitHub identity that owns the repository decision.
4. Return to the workbench after GitHub redirects to the exact callback.
5. Wait until the workbench confirms the retained review.

The callback query is navigation context, not authority. The service consumes
one pending transaction, resolves immutable reviewer identity and current
`maintain` or `admin` evidence, reproduces the proposal, and atomically stores
the review and audit event. The GitHub user token is then discarded. Refreshing
or replaying the callback cannot create a second review.

## 4. Activate The Proposal

1. Recheck the displayed proposal manifest and active revision.
2. Select **Activate proposal**.
3. Confirm the returned authoritative revision.

Activation reloads the exact retained review, requires the reviewed baseline to
still equal the active baseline, reproduces the proposal, and uses the GitHub
App to recheck the retained reviewer's current permission. The active pointer
changes through one compare-and-swap transaction or does not change.

## 5. Interpret Failures

| State                      | Meaning                                                          | Required action                                                            |
|----------------------------|------------------------------------------------------------------|----------------------------------------------------------------------------|
| Sign-in required           | Keycloak session is absent or expired.                           | Sign in again.                                                             |
| Role required              | The current principal lacks `configure` or `activate`.           | Correct Keycloak role assignment; do not widen another credential.         |
| Forbidden reviewer         | GitHub does not prove `maintain` or `admin`.                     | Verify the selected identity, App installation, and repository permission. |
| Stale proposal             | Default head or proposal identity changed.                       | Refresh discovery and inspect the new proposal.                            |
| Baseline conflict          | Active configuration changed during review or before activation. | Refresh the workbench and decide against the new baseline.                 |
| Attestation invalid        | Receipt is absent, expired, mismatched, or permission changed.   | Repeat repository verification.                                            |
| Operation conflict         | An operation id was reused for different facts.                  | Refresh and start a new logical command.                                   |
| Rate limited or overloaded | A bounded admission or provider limit rejected work.             | Retry only after the reported dependency recovers.                         |
| Unavailable                | Required GitHub or PostgreSQL evidence is unknown.               | Preserve FullCI and investigate the dependency.                            |

## Non-Claims

A successful activation does not by itself enable production omission, mutate
GitHub workflow files, prove current CI conformance, or establish production
readiness. Those authorities have separate contracts and evidence.

# Secrets And Incident Prevention Design

Status: future implementation

Date: 2026-06-06

## 1. Feature Question

Can dynamic CI reduce work without accidentally granting excessive credentials
or hiding security-relevant changes?

Answer: yes, if credentials, fixtures, fork contexts, and incident-risk paths
are deterministic policy inputs and never delegated to agent authority.

## 2. Formal Contract

Definitions:

- `CredentialProfile` is a named finite capability set.
- `FixtureProfile` is a named finite test-data set.
- `ExecutionContext = (event, actor, fork, ref, environment, credentialProfile,
  fixtureProfile)`.
- `RiskClass` is a policy-owned label attached to files, graph nodes, checks,
  and known incident patterns.

Invariants:

- S1: forked or untrusted contexts receive no privileged credential profile.
- S2: agent advice cannot introduce a credential profile.
- S3: a changed secret policy, workflow file, dependency lockfile, or deploy
  config is a global risk path.
- S4: unknown risk class maps to FullCI.
- S5: incident-response checks may be added by agent advice but not removed by
  agent advice.

## 3. Why This Is The Best Shape

Claim: credentials must be selected by deterministic policy, not by dynamic
agent reasoning.

Proof:

1. Credential exposure is a security boundary.
2. Security boundaries require explicit authorization.
3. Agent reasoning is not an authorization system.
4. Therefore the agent may describe risk but cannot grant credentials.
5. Deterministic profiles provide the least-privilege authorization boundary.

## 4. Implementation Plan

1. Add config schemas for credential and fixture profiles.
2. Bind every check to allowed profiles.
3. Add fork and actor trust classification.
4. Reject dynamic plans that request profiles outside policy.
5. Treat workflow, secrets policy, dependency lockfile, and deploy config changes
   as global risk paths.
6. Add incident-risk rule packs:
   - dependency supply-chain changes.
   - workflow permission changes.
   - deployment target changes.
   - test fixture or mock changes.
7. Append security-relevant plan and rejection events to the audit ledger.

## 5. Production Gates

- Forked PR plans cannot receive privileged credentials.
- Credential profile changes require explicit policy review.
- Unknown or stale security context maps to FullCI.
- Security escalation cannot be downgraded by agent output.
- Metrics show credential profile use per check and repository.


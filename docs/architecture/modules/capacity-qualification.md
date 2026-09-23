# Capacity Qualification

Status: current module specification

## 1. Purpose

`capacity_qualification` admits externally produced capacity evidence for one
exact deployment epoch. It does not measure the system, own a signing key,
authorize deployment, or change runtime startup.

```text
CapacityEvidence
  -> strict canonical decoding
  -> external signature verification
  -> exact epoch and budget identity
  -> complete metric-domain assessment
  -> CapacityQualified | CapacityNotQualified
```

The owner exists because source configuration can bound resources but cannot
prove that a particular environment satisfies those bounds.

## 2. Authority Boundary

The package may depend only on:

- `kernel` for canonical JSON, hashing, clocks, and Ed25519 verification;
- `audit_replay` for authenticated checkpoint and successor evidence; and
- its own packaged capacity profile.

It cannot import HTTP, application coordination, provider adapters,
persistence, runtime settings, runtime composition, or production-admission
authority. The import-boundary gate enforces this relation.

`production_admission` and `capacity_qualification` remain distinct:

```text
CapacityQualified -/-> ProductionAdmissionGranted
ProductionAdmissionGranted -/-> CapacityQualified

DeploymentEvidenceClaimsCapacity
  -> deploymentEvidence.evidenceDigest = capacityReceiptDigest
```

The equality permits an external production owner to bind the exact receipt;
it does not make either package the semantic owner of the other.

## 3. Capacity Epoch

One receipt binds all of the following:

```text
CapacityEpoch :=
  artifactDigest
  + sourceCommit
  + environmentId
  + databaseIdentityDigest
  + deploymentSubjectDigest
  + runtimeConfigurationDigest
  + ingressControlDigest
  + networkPolicyDigest
  + providerCapacityPolicyDigest
  + retentionPolicyDigest
  + replicaTopology
  + resourceLimits
  + workloadDigest
  + measurementProtocolDigest
  + sampleSetDigest
  + auditStorageInventoryDigest
  + capacityProfileDigest
  + budgetSetDigest
```

A changed conjunct creates a foreign receipt. No implicit monotonicity relation
is currently admitted between epochs.

## 4. Receipt Admission

The exact packaged profile owns the complete metric domain, units, comparison
operator, byte and time limits, and profile-level fixed budgets. Receipt
measurements are non-negative safe integers and contain one sorted unique row
for every metric. Model construction and decoding share the same measurement
cardinality and metric-identity byte bounds, while both signature-payload and
envelope encoding enforce the packaged envelope-byte maximum.

```text
Qualified(r, expected, now) iff
  CanonicalEnvelope(r)
  and Ed25519SignatureValid(r)
  and r.keyId = expected.keyId
  and r.profileDigest = packagedProfileDigest
  and r.epoch = expected.epoch
  and r.budgetSetDigest = expected.budgetSetDigest
  and MetricIds(r) = MetricIds(packagedProfile)
  and FixedBudgets(r) = FixedBudgets(packagedProfile)
  and forall m in r.measurements: m.observedValue <= m.budgetValue
  and r.observedAt <= r.issuedAt
  and r.validFrom <= now < r.validUntil
  and now - r.observedAt <= maximumEvidenceAge
  and EvidenceAgeAndLifetimeBounded(r)
```

Missing, malformed, noncanonical, unsigned, stale, foreign, incomplete, or
budget-violating input returns one closed `not_qualified` result. There is no
boolean default-to-success path.

The affirmative value preserves the exact temporal admission predicate. Its
lower bound is `max(validFrom, issuedAt - maximumClockSkew)`; it remains usable
only while `now < validUntil` and
`now <= observedAt + maximumEvidenceAge`. Admission therefore cannot mint a
value that becomes valid earlier or remains valid longer than the receipt
would be admitted at that trusted time.

The profile fixes `forced-termination-count` and
`safety-invariant-violation-count` budgets at zero. An external signer may
select other environment budgets only when the deployment owner independently
pins their exact budget-set digest.

## 5. Audit Storage Relation

An audit-storage inventory contains an independently identified row for every
live, WAL, backup, and quarantine copy. Every row binds:

- exact content and byte count;
- authenticated checkpoint envelope digest;
- verified successor-chain digest; and
- key-binding inventory digest.

The inventory additionally binds live row count, key-binding count, backup,
restore and replay durations, replay temporary storage, and restore evidence
whose content digest exactly reproduces one identified retained backup.

```text
RetentionPrerequisiteVerified iff
  CheckpointAuthenticatedAndCurrent
  and SuccessorNonEmptyAndValid
  and InventoryDigest = CapacityReceipt.auditStorageInventoryDigest
  and EveryCopyBinds(Checkpoint, Successor, KeyInventory)
  and RestoreEvidence.contentDigest = SelectedBackup.contentDigest
  and CapacityIdentityMatches(Artifact, Database)
  and QualifiedMeasurementsCoverInventory
```

The result is not archive, compaction, deletion, or truncation authority. No
current persistence operation consumes it.

## 6. Failure Semantics

Capacity admission returns fixed codes for missing, malformed, signature,
key, time, foreign-epoch, incomplete-domain, and budget failures. Audit
retention assessment returns fixed codes for expired evidence, identity,
successor, inventory, and capacity failures. Exception text and untrusted
payloads are not part of either result.

## 7. Test Obligations

Native witnesses must falsify at least:

- missing and noncanonical envelopes;
- valid structure with the wrong signature or key;
- exact receipt for another environment;
- missing metric and weakened fixed-zero budget;
- observed value above its signed budget;
- expired evidence;
- forged replay scan;
- checkpoint-link mutation;
- substituted storage copy under the old receipt; and
- inventory larger than the qualified observed envelope.

## 8. Non-Claims

The module does not prove measurement truth, signer custody, external receipt
retention, replica-wide enforcement, production capacity, deployment approval,
safe destructive retention, restore success outside the signed evidence, or
production readiness.

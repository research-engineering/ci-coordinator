import { Boxes } from "lucide-react";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount, formatDateTime } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";

type ComponentPassport = WorkflowDiscoveryReport["componentPassport"];
type ComponentReference = ComponentPassport["references"][number];
type UnresolvedReason = NonNullable<ComponentReference["unresolvedReason"]>;

interface ComponentPassportInventoryProps {
  readonly passport: ComponentPassport;
}

const KIND_LABELS: Readonly<Record<ComponentReference["kind"], string>> = {
  action: "Action",
  docker_image: "Docker image",
  reusable_workflow: "Reusable workflow",
};

const UNRESOLVED_LABELS: Readonly<Record<UnresolvedReason, string>> = {
  annotated_tag_not_dereferenced: "Annotated tag not dereferenced",
  callee_workflow_missing: "Callee workflow missing at revision",
  dynamic_reference: "Dynamic reference",
  installation_inventory_limit_exceeded: "Installation inventory limit reached",
  installation_inventory_unavailable: "Installation inventory unavailable",
  invalid_reference: "Invalid reference",
  local_target_missing: "Local target missing",
  malformed_provider_response: "Malformed provider response",
  not_in_caller_installation: "Not in this installation",
  outside_caller_installation: "Outside this installation",
  provider_binding_mismatch: "Provider binding mismatch",
  provider_limit_exceeded: "Provider tree limit reached",
  provider_unavailable: "Provider unavailable",
  rate_limited: "Provider rate limit reached",
  ref_not_found: "Ref not found",
  registry_not_read: "Registry not read",
  repository_not_found: "Repository not found",
  resolution_deadline_exceeded: "Resolution deadline reached",
  resolution_limit_exceeded: "Resolution limit reached",
  resolution_not_requested: "Resolution not requested",
  resolution_overloaded: "Resolution busy",
  workspace_checkout_dependent: "Depends on workspace checkout",
};

const COLUMNS: readonly DataColumn<ComponentReference>[] = [
  {
    header: "Caller",
    render: (item) => (
      <span className="discovery-primary-cell">
        <code>{item.callerWorkflowPath}</code>
        <span>
          {item.callerJobId}
          {item.stepIndex === null ? " (job call)" : ` step ${item.stepIndex + 1}`}
        </span>
      </span>
    ),
  },
  {
    header: "Component",
    render: (item) => (
      <span className="discovery-primary-cell">
        <strong>{KIND_LABELS[item.kind]}</strong>
        <code>{item.uses}</code>
      </span>
    ),
  },
  {
    header: "Pinned",
    render: (item) => (
      <StatusBadge tone={item.immutable ? "positive" : "warning"}>
        {item.immutable ? "Immutable" : "Mutable"}
      </StatusBadge>
    ),
  },
  {
    header: "Resolution",
    render: (item) => <Resolution item={item} />,
  },
];

export function ComponentPassportInventory({ passport }: ComponentPassportInventoryProps) {
  const references = [...passport.references].sort(compareCallerCoordinate);
  const observed = references.filter((item) => item.resolution === "observed").length;
  return (
    <details className="evidence-section" open>
      <summary>
        <h3>
          <Boxes className="evidence-icon" aria-hidden="true" />
          Dependency passport
        </h3>
        <span>
          {passport.referencesOmitted
            ? "References omitted"
            : `${formatCount(references.length, "reference")}, ${observed} observed`}
        </span>
      </summary>
      {passport.referencesOmitted ? (
        <p className="discovery-warning" role="status">
          The component references exceed the discovery response size limit and are not shown. This
          is not proof that the workflows have no dependencies.
        </p>
      ) : null}
      {passport.declarationsComplete ? null : (
        <p className="discovery-warning" role="status">
          Some workflow sources or steps could not be projected. An empty or short list is not proof
          that the workflows have no other dependencies.
        </p>
      )}
      <DiscoveryTable
        columns={COLUMNS}
        emptyLabel={
          passport.referencesOmitted
            ? "Component references omitted; response size limit reached"
            : passport.declarationsComplete
              ? "No component references declared"
              : "No component references projected; declarations are incomplete"
        }
        keyOf={(item) => item.referenceId}
        label="component reference"
        rows={references}
      />
      <p className="discovery-note">
        Revisions of mutable refs are observed at the shown time and are not retained. Only
        repositories in this installation that you may access are read.
      </p>
    </details>
  );
}

function Resolution({ item }: { readonly item: ComponentReference }) {
  if (item.resolution === "denied") {
    return <StatusBadge tone="negative">Access denied</StatusBadge>;
  }
  if (item.resolution === "unresolved") {
    return (
      <StatusBadge tone="warning">
        {item.unresolvedReason ? UNRESOLVED_LABELS[item.unresolvedReason] : "Unresolved"}
      </StatusBadge>
    );
  }
  return (
    <>
      <StatusBadge tone="positive">Observed</StatusBadge>
      <span className="discovery-primary-cell">
        <code title={item.resolvedRevision ?? undefined}>
          commit {item.resolvedRevision?.slice(0, 12)}
        </code>
        {item.resolvedBlobSha ? (
          <code title={item.resolvedBlobSha}>blob {item.resolvedBlobSha.slice(0, 12)}</code>
        ) : null}
        <span>
          {item.resolvedAt === null
            ? "Bound by caller commit"
            : `Observed ${formatDateTime(item.resolvedAt)}`}
        </span>
      </span>
    </>
  );
}

function compareCallerCoordinate(left: ComponentReference, right: ComponentReference): number {
  const leftKey = [left.callerWorkflowPath, left.callerJobId, left.stepIndex ?? -1] as const;
  const rightKey = [right.callerWorkflowPath, right.callerJobId, right.stepIndex ?? -1] as const;
  if (leftKey[0] !== rightKey[0]) return leftKey[0] < rightKey[0] ? -1 : 1;
  if (leftKey[1] !== rightKey[1]) return leftKey[1] < rightKey[1] ? -1 : 1;
  return leftKey[2] - rightKey[2];
}

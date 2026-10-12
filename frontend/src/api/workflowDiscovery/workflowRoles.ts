import type { components } from "../generated";

type WorkflowDiscoveryReport = components["schemas"]["WorkflowDiscoveryResponse"];
type DecodedCallEdge = WorkflowDiscoveryReport["callEdges"][number];

export type WorkflowRoleCallEdge = Pick<
  DecodedCallEdge,
  | "callerJobId"
  | "callerWorkflowPath"
  | "edgeId"
  | "kind"
  | "remoteRef"
  | "status"
  | "targetPath"
  | "uses"
>;

export interface WorkflowRoleInput {
  readonly callEdges: readonly WorkflowRoleCallEdge[];
  readonly sources: readonly { readonly path: string }[];
  readonly workflows: readonly {
    readonly name: string | null;
    readonly path: string;
    readonly triggers: readonly string[] | null;
  }[];
}

/** Why a workflow cannot be placed in either role from static evidence. */
export type UnclassifiedReason = "no_triggers" | "source_unparsed" | "triggers_unknown";

export interface WorkflowRoleRow {
  /** Every same-snapshot call edge naming this workflow as its local target, in any status. */
  readonly callers: readonly WorkflowRoleCallEdge[];
  readonly component: boolean;
  readonly entrypoint: boolean;
  readonly name: string | null;
  readonly path: string;
  /** Every call edge reachable through resolved local calls, each listed once. */
  readonly reachableCalls: readonly WorkflowRoleCallEdge[];
  readonly triggers: readonly string[] | null;
  readonly unclassifiedReason: UnclassifiedReason | null;
}

export interface WorkflowRoleProjection {
  readonly components: readonly WorkflowRoleRow[];
  readonly entrypoints: readonly WorkflowRoleRow[];
  readonly unclassified: readonly WorkflowRoleRow[];
}

const REUSABLE_TRIGGER = "workflow_call";

/**
 * Projects entrypoint and component roles from declared triggers and call edges only.
 * Paths and filenames never decide a role; a workflow may hold both roles.
 */
export function projectWorkflowRoles(input: WorkflowRoleInput): WorkflowRoleProjection {
  const callsByCaller = groupBy(input.callEdges, (edge) => edge.callerWorkflowPath);
  const callersByTarget = groupBy(
    input.callEdges.filter((edge) => edge.kind === "local" && edge.targetPath !== null),
    (edge) => edge.targetPath ?? "",
  );
  const parsedPaths = new Set(input.workflows.map((workflow) => workflow.path));
  const rows: WorkflowRoleRow[] = input.workflows.map((workflow) => {
    const triggers = workflow.triggers;
    const component = triggers?.includes(REUSABLE_TRIGGER) ?? false;
    const entrypoint = triggers?.some((trigger) => trigger !== REUSABLE_TRIGGER) ?? false;
    return {
      callers: callersByTarget.get(workflow.path) ?? [],
      component,
      entrypoint,
      name: workflow.name,
      path: workflow.path,
      reachableCalls: reachableCalls(workflow.path, callsByCaller),
      triggers,
      unclassifiedReason:
        triggers === null ? "triggers_unknown" : component || entrypoint ? null : "no_triggers",
    };
  });
  const unparsed: WorkflowRoleRow[] = input.sources
    .filter((source) => !parsedPaths.has(source.path))
    .map((source) => ({
      callers: callersByTarget.get(source.path) ?? [],
      component: false,
      entrypoint: false,
      name: null,
      path: source.path,
      reachableCalls: [],
      triggers: null,
      unclassifiedReason: "source_unparsed",
    }));
  return {
    components: rows.filter((row) => row.component),
    entrypoints: rows.filter((row) => row.entrypoint),
    unclassified: [...rows, ...unparsed].filter((row) => row.unclassifiedReason !== null),
  };
}

function reachableCalls(
  root: string,
  callsByCaller: ReadonlyMap<string, readonly WorkflowRoleCallEdge[]>,
): readonly WorkflowRoleCallEdge[] {
  // Breadth-first over resolved local edges; each workflow expands once, so a
  // shared callee or malformed cycle cannot duplicate or loop.
  const expanded = new Set([root]);
  const pending = [root];
  const calls: WorkflowRoleCallEdge[] = [];
  for (let index = 0; index < pending.length; index += 1) {
    for (const edge of callsByCaller.get(pending[index] ?? "") ?? []) {
      calls.push(edge);
      const target = edge.targetPath;
      if (edge.kind === "local" && edge.status === "resolved" && target !== null) {
        if (!expanded.has(target)) {
          expanded.add(target);
          pending.push(target);
        }
      }
    }
  }
  return calls;
}

function groupBy<Item>(
  items: readonly Item[],
  keyOf: (item: Item) => string,
): ReadonlyMap<string, readonly Item[]> {
  const groups = new Map<string, Item[]>();
  for (const item of items) {
    const key = keyOf(item);
    const group = groups.get(key);
    if (group) group.push(item);
    else groups.set(key, [item]);
  }
  return groups;
}

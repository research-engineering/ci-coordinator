import type { components } from "../generated";

type GeneratedPolicyEvent =
  components["schemas"]["WorkflowDiscoveryResponse"]["proposal"]["selectedEvents"][number];

export const SUPPORTED_CI_EVENTS = ["merge_group", "pull_request", "push"] as const;
export type SupportedCiEvent = (typeof SUPPORTED_CI_EVENTS)[number];

type ExactEventContract = [GeneratedPolicyEvent] extends [SupportedCiEvent]
  ? [SupportedCiEvent] extends [GeneratedPolicyEvent]
    ? true
    : false
  : false;

type AssertExact<T extends true> = T;
export type SupportedCiEventContract = AssertExact<ExactEventContract>;

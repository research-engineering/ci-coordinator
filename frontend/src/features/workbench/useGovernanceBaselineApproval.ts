import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveGovernanceBaseline,
  type GovernanceBaselineApprovalCommand,
  type GovernanceBaselineApprovalResult,
} from "../../api/governanceBaseline/client";
import type { GovernanceBaselineRecord } from "../../api/governanceBaseline/schema";
import type { GovernanceObservation } from "../../api/governanceObservation/schema";
import type { WorkbenchScope } from "../../api/workbench/client";

export type GovernanceBaselineSubmissionState =
  | { readonly kind: "idle" }
  | {
      readonly kind: "submitting";
      readonly binding: string;
      readonly command: GovernanceBaselineApprovalCommand;
      readonly receiptContext: string;
    }
  | {
      readonly kind: "settled";
      readonly binding: string;
      readonly command: GovernanceBaselineApprovalCommand;
      readonly receiptContext: string;
      readonly result: GovernanceBaselineApprovalResult;
    };

interface GovernanceBaselineBinding {
  readonly authorityRevision: number;
  readonly baseline: GovernanceBaselineRecord | null;
  readonly csrfToken: string | undefined;
  readonly generation: string;
  readonly onComplete: () => void;
  readonly observation: GovernanceObservation;
  readonly scope: WorkbenchScope;
}

export function useGovernanceBaselineApproval(binding: GovernanceBaselineBinding) {
  const installationId = binding.scope.installationId;
  const repositoryId = binding.scope.repositoryId;
  const baselineKey =
    binding.baseline === null
      ? "absent"
      : [
          binding.baseline.pointer.baselineId,
          binding.baseline.pointer.version,
          binding.baseline.pointer.stateDigest,
        ].join(":");
  const receiptContext = [
    binding.authorityRevision,
    installationId,
    repositoryId,
    binding.observation.stateDigest,
  ].join(":");
  const bindingKey = [receiptContext, baselineKey, binding.generation].join(":");
  const [submissionState, setSubmissionState] = useState<GovernanceBaselineSubmissionState>({
    kind: "idle",
  });
  const requestGeneration = useRef(0);
  const activeBinding = useRef(bindingKey);
  const mutationController = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    activeBinding.current = bindingKey;
    requestGeneration.current += 1;
    mutationController.current?.abort();
    setSubmissionState((current) =>
      current.kind === "settled" &&
      current.result.kind === "complete" &&
      current.receiptContext === receiptContext
        ? current
        : { kind: "idle" },
    );
    return () => {
      requestGeneration.current += 1;
      mutationController.current?.abort();
    };
  }, [bindingKey, receiptContext]);

  const submitCommand = useCallback(
    (command: GovernanceBaselineApprovalCommand) => {
      if (!binding.csrfToken) return;
      const generation = ++requestGeneration.current;
      mutationController.current?.abort();
      const controller = new AbortController();
      mutationController.current = controller;
      setSubmissionState({
        binding: bindingKey,
        command,
        kind: "submitting",
        receiptContext,
      });
      void approveGovernanceBaseline(command, binding.csrfToken, controller.signal).then(
        (result) => {
          if (requestGeneration.current !== generation || activeBinding.current !== bindingKey) {
            return;
          }
          setSubmissionState({
            binding: bindingKey,
            command,
            kind: "settled",
            receiptContext,
            result,
          });
          if (result.kind === "complete") binding.onComplete();
        },
        () => {
          if (requestGeneration.current === generation && activeBinding.current === bindingKey) {
            setSubmissionState({
              binding: bindingKey,
              command,
              kind: "settled",
              receiptContext,
              result: { kind: "network-failure" },
            });
          }
        },
      );
    },
    [binding.csrfToken, binding.onComplete, bindingKey, receiptContext],
  );

  const currentSubmission =
    (submissionState.kind === "settled" &&
      submissionState.result.kind === "complete" &&
      submissionState.receiptContext === receiptContext) ||
    submissionState.kind === "idle" ||
    submissionState.binding === bindingKey
      ? submissionState
      : { kind: "idle" as const };
  const submit = useCallback(
    (reason: string) => {
      submitCommand({
        expectedActive: binding.baseline?.pointer ?? null,
        expectedStateDigest: binding.observation.stateDigest,
        operationId: crypto.randomUUID(),
        reason,
        scope: { installationId, repositoryId },
      });
    },
    [
      binding.baseline,
      binding.observation.stateDigest,
      installationId,
      repositoryId,
      submitCommand,
    ],
  );
  const retry = useCallback(() => {
    if (currentSubmission.kind === "settled" && currentSubmission.result.kind !== "complete") {
      submitCommand(currentSubmission.command);
    }
  }, [currentSubmission, submitCommand]);

  return {
    retry,
    submission: currentSubmission,
    submit,
  } as const;
}

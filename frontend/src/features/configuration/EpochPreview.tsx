import { useEffect, useState } from "react";
import { readConfigPreview } from "../../api/configLifecycle/client";
import {
  type ConfigPreview,
  type ConfigScope,
  MAX_PREVIEW_POINTERS,
} from "../../api/configLifecycle/schema";
import type { LifecycleResult } from "../../api/configLifecycle/transport";
import { ConfigurationNotice } from "./ConfigurationNotice";

const COVERAGE: Readonly<Record<ConfigPreview["coverageRelation"], string>> = {
  equal: "Equal validation coverage",
  greater: "Greater validation coverage",
  less: "Reduced validation coverage",
  incomparable: "Coverage is not comparable",
  unknown: "Coverage relation is unknown",
};

// Reads the server preview only while enabled; disabling aborts the read and drops its result,
// so a re-enabled view reads again. An undefined base epoch performs no read.
export function useEpochPreview(
  scope: ConfigScope,
  baseEpochId: string | undefined,
  targetEpochId: string,
  enabled: boolean,
): LifecycleResult<ConfigPreview> | undefined {
  const key =
    baseEpochId === undefined
      ? undefined
      : `${scope.installationId}:${scope.repositoryId}:${baseEpochId}:${targetEpochId}`;
  const [received, setReceived] = useState<{
    readonly key: string;
    readonly result: LifecycleResult<ConfigPreview>;
  }>();
  const [wasEnabled, setWasEnabled] = useState(enabled);
  if (wasEnabled !== enabled) {
    setWasEnabled(enabled);
    setReceived(undefined);
  }
  useEffect(() => {
    if (!enabled || baseEpochId === undefined || key === undefined) return;
    const controller = new AbortController();
    void readConfigPreview(scope, baseEpochId, targetEpochId, controller.signal).then((result) => {
      if (!controller.signal.aborted) setReceived({ key, result });
    });
    return () => controller.abort();
  }, [scope, baseEpochId, targetEpochId, key, enabled]);
  return enabled && received !== undefined && received.key === key ? received.result : undefined;
}

// Read-only projection of the server preview; it grants no authority and the server rechecks
// coverage and revision at command time.
export function EpochPreview({
  label,
  result,
}: {
  readonly label: string;
  readonly result: LifecycleResult<ConfigPreview> | undefined;
}) {
  return (
    <section className="configuration-review" aria-label={label}>
      <h4>{label}</h4>
      {!result ? (
        <p role="status">Comparing retained epochs</p>
      ) : result.kind !== "ready" ? (
        <ConfigurationNotice failure={result} />
      ) : (
        <>
          <dl>
            <dt>Coverage</dt>
            <dd>{COVERAGE[result.value.coverageRelation]}</dd>
            <dt>Changed policy fields</dt>
            <dd>
              {result.value.diffState === "complete"
                ? result.value.changedPointers.length
                : `More than ${MAX_PREVIEW_POINTERS.toLocaleString("en-US")}`}
            </dd>
            <dt>Diff identity</dt>
            <dd>
              <code>{result.value.diffHash ?? "Not available"}</code>
            </dd>
          </dl>
          {result.value.diffState === "complete" && result.value.changedPointers.length > 0 ? (
            <ul aria-label="Changed policy fields">
              {result.value.changedPointers.map((pointer) => (
                <li key={pointer}>
                  <code>{pointer || "/"}</code>
                </li>
              ))}
            </ul>
          ) : null}
          {result.value.diffState === "complete" && result.value.changedPointers.length === 0 ? (
            <p>No semantic policy change.</p>
          ) : null}
          <p>
            Fields are policy schema locations without values. The server rechecks coverage and
            revision when a command is submitted.
          </p>
        </>
      )}
    </section>
  );
}

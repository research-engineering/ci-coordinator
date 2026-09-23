import { useLayoutEffect, useRef, useState } from "react";
import { readConfigStatus } from "../../api/configLifecycle/client";
import type { ConfigScope, ConfigStatus } from "../../api/configLifecycle/schema";
import type { LifecycleResult } from "../../api/configLifecycle/transport";

export function useConfigurationStatus(
  scope: ConfigScope,
  enabled: boolean,
  minimumActive: ConfigStatus["active"] | undefined,
  readRevision: number,
) {
  const [cursors, setCursors] = useState<readonly (string | null)[]>([null]);
  const [version, setVersion] = useState(0);
  const [received, setReceived] = useState<{
    readonly key: string;
    readonly result: LifecycleResult<ConfigStatus>;
  }>();
  const latestActive = useRef<ConfigStatus["active"] | undefined>(undefined);
  const after = cursors.at(-1) ?? null;
  const key = `${scope.installationId}:${scope.repositoryId}:${enabled}:${after}:${version}:${minimumActive?.revision}:${minimumActive?.epochId}:${readRevision}`;
  useLayoutEffect(() => {
    setReceived(undefined);
    if (
      minimumActive &&
      (!latestActive.current || minimumActive.revision >= latestActive.current.revision)
    )
      latestActive.current = minimumActive;
    if (!enabled) {
      setCursors((current) => (current.length === 1 ? current : [null]));
      return;
    }
    const controller = new AbortController();
    void readConfigStatus(scope, after, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.kind === "ready") {
        const previous = latestActive.current;
        const next = result.value.active;
        if (
          previous &&
          (!next ||
            next.revision < previous.revision ||
            (next.revision === previous.revision && next.epochId !== previous.epochId))
        ) {
          setReceived({ key, result: { kind: "invalid-response" } });
          return;
        }
        latestActive.current = next;
        if (
          after !== null &&
          previous !== undefined &&
          (previous?.revision !== next?.revision || previous?.epochId !== next?.epochId)
        ) {
          setCursors([null]);
          return;
        }
      }
      setReceived({ key, result });
    });
    return () => controller.abort();
  }, [scope, after, enabled, key, minimumActive]);
  function refresh() {
    setCursors([null]);
    setVersion((value) => value + 1);
  }
  const result = enabled && received?.key === key ? received.result : undefined;
  return {
    result,
    refresh,
    key,
    page: cursors.length,
    next: () => {
      if (result?.kind === "ready" && result.value.nextCursor !== null)
        setCursors([...cursors, result.value.nextCursor]);
    },
    previous: () => {
      if (cursors.length > 1) setCursors(cursors.slice(0, -1));
    },
  };
}

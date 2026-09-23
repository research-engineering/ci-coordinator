import { type MouseEvent, useCallback, useEffect, useState } from "react";
import { type ConsoleRoute, consoleHref, readConsoleRoute, sameScope } from "./navigation";

export function useConsoleNavigation() {
  const [route, setRoute] = useState(() => readConsoleRoute(location.search));
  const adoptLocation = useCallback(() => {
    const next = readConsoleRoute(location.search);
    setRoute((previous) => ({
      ...next,
      scope: sameScope(previous.scope, next.scope) ? previous.scope : next.scope,
      ...(sameScope(previous.scope, next.scope) &&
      next.view !== "economics" &&
      previous.economicsTab
        ? { economicsTab: previous.economicsTab }
        : {}),
      ...(sameScope(previous.scope, next.scope) &&
      next.view !== "activity" &&
      previous.activitySource
        ? { activitySource: previous.activitySource }
        : {}),
    }));
  }, []);

  useEffect(() => {
    addEventListener("popstate", adoptLocation);
    return () => removeEventListener("popstate", adoptLocation);
  }, [adoptLocation]);

  function navigate(route: ConsoleRoute) {
    const href = consoleHref(route, location.search);
    if (`${location.pathname}${location.search}` === href) return;
    history.pushState(null, "", href);
    adoptLocation();
  }

  function followLink(event: MouseEvent<HTMLAnchorElement>) {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    const target = new URL(event.currentTarget.href);
    if (target.origin !== location.origin || target.pathname !== "/workbench") return;
    event.preventDefault();
    navigate(readConsoleRoute(target.search));
  }

  return { route, followLink, navigate } as const;
}

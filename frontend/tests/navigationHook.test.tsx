import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { useConsoleNavigation } from "../src/features/workbench/useConsoleNavigation";

afterEach(() => history.replaceState(null, "", "/workbench"));

test("subview memory is scope-bound while history adopts the active URL", () => {
  const prefix = "/workbench?installationId=1&repositoryId=2&limit=10";
  history.replaceState(null, "", `${prefix}&view=economics&economicsTab=history`);
  const { result } = renderHook(useConsoleNavigation);
  expect(result.current.route.economicsTab).toBe("history");
  act(() => result.current.navigate({ ...result.current.route, view: "overview" }));
  expect(location.search).not.toContain("economicsTab");
  expect(result.current.route.economicsTab).toBe("history");
  act(() => result.current.navigate({ ...result.current.route, view: "economics" }));
  expect(location.search).toContain("economicsTab=history");
  act(() => {
    history.replaceState(null, "", `${prefix}&view=economics`);
    dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current.route.economicsTab).toBeUndefined();
  act(() =>
    result.current.navigate({
      ...result.current.route,
      view: "activity",
      activitySource: "business",
    }),
  );
  expect(location.search).toContain("activitySource=business");
  act(() =>
    result.current.navigate({
      ...result.current.route,
      scope: { installationId: 1, repositoryId: 3, limit: 10 },
      view: "overview",
    }),
  );
  expect(result.current.route.activitySource).toBeUndefined();
  expect(result.current.route.economicsTab).toBeUndefined();
});

test("same-scope navigation preserves object identity and does not duplicate history", () => {
  history.replaceState(
    null,
    "",
    "/workbench?installationId=1&repositoryId=2&limit=10&view=overview",
  );
  const { result, unmount } = renderHook(useConsoleNavigation);
  const scope = result.current.route.scope;
  const priorLength = history.length;
  act(() => result.current.navigate({ scope, view: "workflows", tab: "plans" }));
  expect(result.current.route.view).toBe("workflows");
  expect(result.current.route.scope).toBe(scope);
  expect(history.length).toBe(priorLength + 1);
  act(() => result.current.navigate({ scope, view: "workflows", tab: "plans" }));
  expect(history.length).toBe(priorLength + 1);
  act(() => {
    history.replaceState(
      null,
      "",
      "/workbench?installationId=1&repositoryId=2&limit=10&view=audit",
    );
    dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current.route.scope).toBe(scope);
  expect(result.current.route.view).toBe("audit");
  act(() => {
    history.replaceState(null, "", "/workbench?installationId=1&repositoryId=3&limit=10");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current.route.scope).not.toBe(scope);
  expect(result.current.route.scope?.repositoryId).toBe(3);
  unmount();
  dispatchEvent(new PopStateEvent("popstate"));
});

test.each([
  ["plain", {}, false, "/workbench?installationId=1&repositoryId=2&limit=10&view=workflows", true],
  ["meta", { metaKey: true }, false, "/workbench", false],
  ["control", { ctrlKey: true }, false, "/workbench", false],
  ["shift", { shiftKey: true }, false, "/workbench", false],
  ["alt", { altKey: true }, false, "/workbench", false],
  ["middle", { button: 1 }, false, "/workbench", false],
  ["cancelled", {}, true, "/workbench", false],
  ["foreign", {}, false, "https://foreign.example/workbench", false],
  ["other path", {}, false, "/api/v1/auth/keycloak/start", false],
] as const)(
  "preserves native %s link semantics",
  (_name, modifiers, cancelled, href, intercepted) => {
    let observed = false;
    function Harness() {
      const navigation = useConsoleNavigation();
      return (
        <>
          <a
            href={href}
            onClick={(event) => {
              if (cancelled) event.preventDefault();
              navigation.followLink(event);
              observed = !cancelled && event.defaultPrevented;
              event.preventDefault();
            }}
          >
            Destination
          </a>
          <output>{navigation.route.view}</output>
        </>
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("link"), modifiers);
    expect(observed).toBe(intercepted);
    expect(screen.getByRole("status")).toHaveTextContent(
      intercepted ? "workflows" : "repositories",
    );
  },
);

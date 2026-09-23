import type { BrowserContext } from "@playwright/test";
import { SCENARIOS, type ScenarioName } from "./scenarioNames";

export async function installDemoControls(context: BrowserContext, name: ScenarioName) {
  await context.addInitScript(
    ({ scenarios, selected }) => {
      // Worker/frame creation is also denied by CSP; WebRTC has no equivalent fetch route.
      for (const capability of [
        "RTCPeerConnection",
        "webkitRTCPeerConnection",
        "WebTransport",
        "Worker",
        "SharedWorker",
      ]) {
        Object.defineProperty(globalThis, capability, {
          configurable: false,
          writable: false,
          value: class {
            constructor() {
              throw new Error("Synthetic session: transport blocked");
            }
          },
        });
      }
      document.addEventListener("DOMContentLoaded", () => {
        const host = document.createElement("section");
        host.setAttribute("aria-label", "Synthetic session");
        const shadow = host.attachShadow({ mode: "open" });
        const style = document.createElement("style");
        style.textContent = `:host{display:block;position:sticky;top:0;z-index:1000}
        section{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:10px;background:#fff4ce;color:#28210c;font:14px system-ui}
        label{display:flex;gap:6px;align-items:center;max-width:100%}select{min-width:0;max-width:100%}button,select{font:inherit;padding:5px}p{flex-basis:100%;margin:0}`;
        const bar = document.createElement("section");
        const marker = document.createElement("strong");
        marker.textContent = "Synthetic session";
        const label = document.createElement("label");
        label.textContent = "Scenario";
        const select = document.createElement("select");
        select.setAttribute("aria-label", "Scenario");
        for (const [value, text] of Object.entries(scenarios)) select.add(new Option(text, value));
        select.value = selected;
        label.append(select);
        const help = document.createElement("p");
        help.textContent =
          selected === "stale-navigation"
            ? "Open Repositories, select synthetic-service, open CI economics, then release the old response."
            : selected === "provider-failure-retry"
              ? "The first provider inventory read fails. Retry to recover; Reset session repeats the failure."
              : "Synthetic data only. Reset creates a new browser context. Close session to finish.";
        const send = (action: string, scenario = select.value) => {
          const binding = Reflect.get(window, "__ciDemoControl") as (command: {
            action: string;
            scenario: string;
          }) => Promise<void>;
          void binding({ action, scenario }).catch(() => {
            help.textContent = "Synthetic session closed or unavailable.";
          });
        };
        select.addEventListener("change", () => send("select"));
        bar.append(marker, label);
        for (const [text, action] of [
          ["Reset session", "reset"],
          ["Release old response", "release"],
          ["Close session", "close"],
        ] as const) {
          if (action === "release" && selected !== "stale-navigation") continue;
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = text;
          button.addEventListener("click", () => send(action));
          bar.append(button);
        }
        bar.append(help);
        shadow.append(style, bar);
        document.body.prepend(host);
      });
    },
    { scenarios: SCENARIOS, selected: name },
  );
}

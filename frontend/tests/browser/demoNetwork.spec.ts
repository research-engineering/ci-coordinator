import { once } from "node:events";
import { createServer } from "node:http";
import { connect, type Socket } from "node:net";
import { setImmediate as nextTurn } from "node:timers/promises";
import { expect } from "@playwright/test";
import { installSyntheticNetwork } from "../../dev/network";
import { createScenario } from "../../dev/scenarios";
import { confirmPageClosure, openDemoSession } from "../../dev/session";
import { test } from "./demoFixture";

async function canary(redirect?: string) {
  const requests: string[] = [];
  let connections = 0;
  let closing: Promise<void> | undefined;
  const server = createServer((request, response) => {
    requests.push(request.url ?? "");
    response.setHeader("Cache-Control", "no-store");
    if (request.url === "/src/worker-probe.js") {
      response.writeHead(200, { "content-type": "text/javascript" });
      response.end('postMessage("worker-executed");');
    } else if (request.url === "/src/service-worker-probe.js") {
      response.writeHead(200, {
        "content-type": "text/javascript",
        "service-worker-allowed": "/",
      });
      response.end(`self.addEventListener("install", event => {
        event.waitUntil(fetch("/service-worker-executed").then(() => self.skipWaiting()));
      });
      self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));`);
    } else if (redirect && request.url === "/src/redirect.js") {
      response.writeHead(302, { location: redirect });
      response.end();
    } else {
      response.writeHead(200, { "content-type": "text/html" });
      response.end(
        redirect
          ? '<!doctype html><script src="/src/redirect.js"></script>'
          : "<!doctype html><title>Network canary</title>",
      );
    }
  });
  server.on("connection", () => connections++);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Canary did not bind loopback");
  return {
    origin: `http://127.0.0.1:${address.port}`,
    server,
    requests,
    get connections() {
      return connections;
    },
    close: () =>
      (closing ??= new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
        // close() alone can wait for an accepted socket that has not sent a
        // complete request. Stop accepting first, then reap this canary's peers.
        server.closeAllConnections();
      })),
  };
}

test("unhandled APIs, encoded paths, methods and foreign network fail closed", async ({ demo }) => {
  const peer = await canary();
  try {
    expect((await fetch(`${peer.origin}/positive-control`)).status).toBe(200);
    peer.requests.length = 0;
    const results = await demo.page.evaluate(async (origin) => {
      const paths = [
        "/api/v1/unknown",
        "/api",
        "/%61pi/v1/auth/session",
        "/auth/login",
        "/api/v1/workbench/installations?unexpected=1",
        "/api/v1/workbench/installations?page=1&page=1&perPage=30",
        "/api/v1/workbench/installations?page=2&perPage=30",
        "/api/v1/workbench/installations?page=1&perPage=100",
        "/api/v1/auth/session?x=1&x=2",
        `${origin}/fetch`,
        "https://example.invalid/external",
      ];
      return Promise.all([
        ...paths.map((path) =>
          fetch(path).then(
            () => false,
            () => true,
          ),
        ),
        fetch("/api/v1/workbench/installations", { method: "POST", body: "{}" }).then(
          () => false,
          () => true,
        ),
        new Promise<boolean>((resolve) => {
          const image = new Image();
          image.onload = () => resolve(false);
          image.onerror = () => resolve(true);
          image.src = `${origin}/image`;
          document.body.append(image);
        }),
      ]);
    }, peer.origin);
    expect(results).toEqual(Array(13).fill(true));
    expect(demo.blocked).toContain("unhandled-api");
    expect(demo.blocked).toContain("unhandled-resource");
    expect(peer.requests).toEqual([]);
    expect((await fetch(`${peer.origin}/still-listening`)).status).toBe(200);
    expect(peer.requests).toEqual(["/still-listening"]);
  } finally {
    await peer.close();
  }
});

test("the first popup request and direct navigation cannot reach another origin", async ({
  demo,
}) => {
  const peer = await canary();
  try {
    const failed = demo.context.waitForEvent(
      "requestfailed",
      (request) => request.url() === `${peer.origin}/popup`,
    );
    const opened = demo.context.waitForEvent("page");
    await demo.page.evaluate((url) => {
      window.open(url);
    }, `${peer.origin}/popup`);
    await failed;
    await (await opened).close();
    const page = await demo.context.newPage();
    await expect(page.goto(`${peer.origin}/navigation`)).rejects.toThrow();
    await page.close();
    expect(peer.requests).toEqual([]);
    expect(demo.blocked).toContain("foreign-origin");
  } finally {
    await peer.close();
  }
});

test("service workers, workers, websocket and non-fetch transports are blocked", async ({
  browser,
  viewport,
  isMobile,
  hasTouch,
  deviceScaleFactor,
}) => {
  const display = {
    viewport,
    isMobile,
    hasTouch,
    ...(deviceScaleFactor === undefined ? {} : { deviceScaleFactor }),
  };
  await test.step("the worker probes execute in an unguarded context", async () => {
    const peer = await canary();
    try {
      const control = await browser.newContext({ ...display, serviceWorkers: "allow" });
      try {
        const page = await control.newPage();
        await page.goto(`${peer.origin}/workbench`);
        const observed = await page.evaluate(async () => {
          const worker = new Promise<string>((resolve, reject) => {
            const instance = new Worker("/src/worker-probe.js");
            instance.onmessage = (event) => {
              instance.terminate();
              resolve(event.data);
            };
            instance.onerror = () => reject(new Error("Worker positive control did not execute"));
          });
          await navigator.serviceWorker.register("/src/service-worker-probe.js", { scope: "/" });
          const registration = await navigator.serviceWorker.ready;
          return { worker: await worker, serviceWorker: registration.active?.scriptURL };
        });
        expect(observed).toEqual({
          worker: "worker-executed",
          serviceWorker: `${peer.origin}/src/service-worker-probe.js`,
        });
        expect(peer.requests).toContain("/src/worker-probe.js");
        expect(peer.requests).toContain("/src/service-worker-probe.js");
        expect(peer.requests).toContain("/service-worker-executed");
      } finally {
        await control.close();
      }
    } finally {
      await peer.close();
    }
  });

  // Serve the same valid probes at the owned origin through the real session
  // factory. Do not duplicate its context options or install its guards here.
  const peer = await canary();
  try {
    const session = await openDemoSession(browser, peer, "populated", display);
    const { context, page } = session;
    const starts: string[] = [];
    try {
      context.on("serviceworker", (worker) => starts.push(worker.url()));
      page.on("worker", (worker) => starts.push(worker.url()));
      await expect(page.getByRole("region", { name: "Synthetic session" })).toBeVisible();
      const results = await page.evaluate(async () => {
        const worker = new Promise<boolean>((resolve) => {
          try {
            const instance = new Worker("/src/worker-probe.js");
            instance.onmessage = () => {
              instance.terminate();
              resolve(false);
            };
            instance.onerror = (event) => {
              event.preventDefault();
              instance.terminate();
              resolve(true);
            };
          } catch {
            resolve(true);
          }
        });
        const websocket = new Promise<boolean>((resolve) => {
          const socket = new WebSocket(`${location.origin.replace("http:", "ws:")}/api/unhandled`);
          socket.onopen = () => {
            socket.close();
            resolve(false);
          };
          socket.onerror = () => resolve(true);
          socket.onclose = () => resolve(true);
        });
        let rtcBlocked = false;
        try {
          new RTCPeerConnection().close();
        } catch {
          rtcBlocked = true;
        }
        // A blocked registration can resolve without installing a worker.
        // Inspect effects, not whether that promise rejects.
        const registration = await navigator.serviceWorker
          .register("/src/service-worker-probe.js", { scope: "/" })
          .catch(() => undefined);
        return {
          returnedRegistration: registration !== undefined,
          registrations: (await navigator.serviceWorker.getRegistrations()).length,
          controlled: navigator.serviceWorker.controller !== null,
          workerBlocked: await worker,
          websocketBlocked: await websocket,
          rtcBlocked,
        };
      });
      expect(results).toEqual({
        returnedRegistration: false,
        registrations: 0,
        controlled: false,
        workerBlocked: true,
        websocketBlocked: true,
        rtcBlocked: true,
      });
      expect(context.serviceWorkers()).toEqual([]);
      expect(page.workers()).toEqual([]);
    } finally {
      await session.close();
    }
    // Context closure drains the attempts before checking the complete observed
    // lifetime: no worker start, script download or service-worker execution.
    expect(starts).toEqual([]);
    expect(peer.requests).toEqual(["/workbench"]);
    expect(session.errors).toEqual([]);
  } finally {
    await peer.close();
  }
});

test("a document outside the owned origin is closed even without an HTTP request", async ({
  demo,
}) => {
  const created = demo.context.waitForEvent("page");
  const page = await demo.context.newPage();
  expect(await created).toBe(page);
  await page.goto("data:text/html,synthetic-navigation-escape").catch(() => undefined);
  await expect.poll(() => page.isClosed()).toBe(true);
  await expect(demo.page.getByRole("region", { name: "Synthetic session" })).toBeVisible();
});

for (const fault of ["acknowledged", "pending", "closed-with-pending-request"] as const) {
  test(`foreign-page closure recovers from a ${fault} renderer request`, async ({ demo }) => {
    const page = await demo.context.newPage();
    const original = page.close.bind(page);
    const calls: boolean[] = [];
    page.close = (options = {}) => {
      calls.push(options.runBeforeUnload === true);
      if (options.runBeforeUnload) {
        return fault === "acknowledged" ? Promise.resolve() : new Promise<void>(() => {});
      }
      const closing = original(options);
      return fault === "closed-with-pending-request"
        ? closing.then(() => new Promise<void>(() => {}))
        : closing;
    };
    const closed = page.waitForEvent("close");
    const urls = [
      "data:text/html,synthetic-navigation-escape",
      "data:text/html,second-foreign-navigation",
    ];
    const navigations: string[] = [];
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame() && !page.isClosed()) navigations.push(frame.url());
    });
    for (const url of urls) await page.goto(url, { waitUntil: "commit" });
    expect(navigations).toEqual(urls);
    await closed;
    expect(page.isClosed()).toBe(true);
    expect(calls).toEqual([true, false]);
    await expect(demo.page.getByRole("region", { name: "Synthetic session" })).toBeVisible();
    expect(demo.errors).toEqual([]);
  });
}

test("actual close completes the sequence while its request remains pending", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const pending = Promise.withResolvers<void>();
  try {
    const page = await context.newPage();
    const original = page.close.bind(page);
    page.close = (options = {}) => {
      if (options.runBeforeUnload) return Promise.resolve();
      void original(options).catch(pending.reject);
      return pending.promise;
    };
    let state = "pending";
    const sequence = confirmPageClosure(page);
    void sequence.then(
      () => {
        state = "closed";
      },
      () => {
        state = "failed";
      },
    );
    await expect.poll(() => state).toBe("closed");
    expect(page.isClosed()).toBe(true);
    pending.reject(new Error("Late close request rejection"));
    await nextTurn();
    await sequence;
  } finally {
    pending.resolve();
    await context.close();
  }
});

test("unconfirmed foreign-page closure fails and closes only its owned context", async ({
  browser,
  demoServer,
}) => {
  const session = await openDemoSession(browser, demoServer, "populated");
  const other = await browser.newContext();
  try {
    const unaffected = await other.newPage();
    const page = await session.context.newPage();
    const calls: boolean[] = [];
    page.close = async (options = {}) => {
      calls.push(options.runBeforeUnload === true);
    };
    const closed = session.context.waitForEvent("close");
    await page.goto("data:text/html,unclosable-foreign-document").catch(() => undefined);
    await session.finished;
    await closed;
    expect(calls).toEqual([true, false]);
    expect(page.isClosed()).toBe(true);
    expect(session.errors).toHaveLength(1);
    expect(browser.isConnected()).toBe(true);
    expect(unaffected.isClosed()).toBe(false);
  } finally {
    await other.close();
    await session.close();
  }
});

test("APIRequestContext and route.continue bypasses hit the rejecting proxy", async ({
  demo,
  demoServer,
}) => {
  const peer = await canary();
  try {
    for (const url of [`${peer.origin}/api-request`, `${demoServer.origin}/api/v1/auth/session`]) {
      await test.step(`CONNECT denial: ${new URL(url).pathname}`, async () => {
        const response = await demo.context.request.get(url, { timeout: 5_000, maxRedirects: 0 });
        try {
          expect(response.status()).toBe(403);
          expect(await response.body()).toHaveLength(0);
        } finally {
          await response.dispose();
        }
      });
    }
    const target = `${peer.origin}/route-bypass`;
    await demo.context.route(target, (route) => route.continue());
    const page = await demo.context.newPage();
    try {
      await test.step("continued navigation receives 403 before the foreign page closes", async () => {
        const denied = page.waitForResponse(target, { timeout: 5_000 });
        const [response] = await Promise.all([
          denied,
          page.goto(target, { waitUntil: "commit", timeout: 5_000 }).catch((error: unknown) => {
            if (!page.isClosed()) throw error;
          }),
        ]);
        expect(response.status()).toBe(403);
        await expect.poll(() => page.isClosed()).toBe(true);
      });
    } finally {
      await page.close();
    }
    expect(peer.requests).toEqual([]);
    expect(peer.connections).toBe(0);
    const control = await fetch(`${peer.origin}/positive-control`);
    expect(control.status).toBe(200);
    await control.arrayBuffer();
    expect(peer.requests).toEqual(["/positive-control"]);
  } finally {
    await peer.close();
  }
});

test("the first proxy-denied popup document is closed without reaching its origin", async ({
  demo,
}) => {
  const peer = await canary();
  try {
    const target = `${peer.origin}/popup-bypass`;
    await demo.context.route(target, (route) => route.continue());
    const opened = demo.context.waitForEvent("page", { timeout: 5_000 });
    const denied = demo.context.waitForEvent("response", {
      predicate: (response) => response.url() === target,
      timeout: 5_000,
    });
    await demo.page.evaluate((url) => {
      window.open(url);
    }, target);
    const popup = await opened;
    expect((await denied).status()).toBe(403);
    await expect.poll(() => popup.isClosed()).toBe(true);
    expect(peer.requests).toEqual([]);
    expect(peer.connections).toBe(0);
    await expect(demo.page.getByRole("region", { name: "Synthetic session" })).toBeVisible();
  } finally {
    await peer.close();
  }
});

test("scenario delivery uses completed responses without a remote response lookup", async ({
  context,
}) => {
  const peer = await canary();
  const scenario = createScenario("provider-failure-retry");
  const path = "/api/v1/workbench/installations";
  const target = `${peer.origin}${path}?page=1&perPage=30`;
  const events: string[] = [];
  const errors: unknown[] = [];
  let lookups = 0;
  let failDelivery = false;
  const failure = new Error("Delivery callback failed");
  const confirmDelivery = scenario.confirmDelivery;
  scenario.confirmDelivery = (deliveredPath, status) => {
    events.push(`delivered:${status}`);
    if (failDelivery) throw failure;
    confirmDelivery(deliveredPath, status);
  };
  context.on("request", (request) => {
    request.response = async () => {
      lookups++;
      throw new Error("Delivery must not issue a response RPC, even before page closure");
    };
  });
  context.on("response", (response) => {
    if (response.url() === target) events.push("headers");
  });
  context.on("requestfinished", (request) => {
    if (request.url() === target) events.push("finished");
  });
  try {
    await installSyntheticNetwork(context, peer.origin, scenario, (error) => errors.push(error));
    const page = await context.newPage();
    await page.goto(`${peer.origin}/workbench`);
    for (const unowned of [
      `${target}&bypass=1`,
      `${peer.origin.replace("127.0.0.1", "localhost")}${path}`,
    ]) {
      await context.route(unowned, (route) => route.fulfill({ status: 503, body: "Unowned" }));
      const finished = context.waitForEvent(
        "requestfinished",
        (request) => request.url() === unowned,
      );
      expect((await page.goto(unowned))?.status()).toBe(503);
      await finished;
    }
    expect(events).toEqual([]);
    await page.goto(`${peer.origin}/workbench`);

    const readInventory = async () => {
      const finished = context.waitForEvent(
        "requestfinished",
        (request) => request.url() === target,
      );
      const status = await page.evaluate(async (url) => {
        const response = await fetch(url);
        await response.text();
        return response.status;
      }, target);
      await finished;
      return status;
    };
    expect(await readInventory()).toBe(503);
    expect(await readInventory()).toBe(200);
    expect(events).toEqual([
      "headers",
      "finished",
      "delivered:503",
      "headers",
      "finished",
      "delivered:200",
    ]);
    expect(errors).toEqual([]);

    failDelivery = true;
    expect(await readInventory()).toBe(200);
    await context.close();
    expect(lookups).toBe(0);
    expect(errors).toEqual([failure]);
  } finally {
    scenario.dispose();
    await context.close();
    await peer.close();
  }
});

test("provider failure waits for host delivery before becoming available to Retry", async ({
  browser,
}) => {
  const peer = await canary();
  try {
    const session = await openDemoSession(browser, peer, "provider-failure-retry");
    const confirmation = Promise.withResolvers<() => void>();
    const waiting = Promise.withResolvers<void>();
    const confirm = session.scenario.confirmDelivery;
    session.scenario.confirmDelivery = (path, status) => {
      if (path === "/api/v1/workbench/installations" && status === 503)
        confirmation.resolve(() => confirm(path, status));
      else confirm(path, status);
    };
    try {
      const page = session.page;
      await page.exposeFunction("__testProviderWait", () => waiting.resolve());
      await page.evaluate(() => {
        const control = Reflect.get(window, "__ciDemoControl") as (value: {
          action: string;
        }) => Promise<void>;
        Reflect.set(window, "__ciDemoControl", async (value: { action: string }) => {
          if (value.action === "provider-delivered") {
            const waiting = Reflect.get(window, "__testProviderWait") as () => Promise<void>;
            await waiting();
          }
          await control(value);
        });
      });
      expect(
        await page.evaluate(async () => {
          const controller = new AbortController();
          const response = fetch("/api/v1/workbench/installations?page=1&perPage=30", {
            signal: controller.signal,
          });
          controller.abort();
          return response.then(
            () => "returned",
            () => "aborted",
          );
        }),
      ).toBe("aborted");
      const received = page.evaluate(async () => {
        const response = await fetch("/api/v1/workbench/installations?page=1&perPage=30");
        document.body.dataset["providerStatus"] = String(response.status);
        return response.status;
      });
      await Promise.race([
        waiting.promise,
        received.then(() => {
          throw new Error("Provider failure reached the UI before the delivery barrier");
        }),
      ]);
      const release = await confirmation.promise;
      expect(await page.getAttribute("body", "data-provider-status")).toBeNull();
      release();
      expect(await received).toBe(503);
      expect(
        await page.evaluate(
          async () => (await fetch("/api/v1/workbench/installations?page=1&perPage=30")).status,
        ),
      ).toBe(200);
    } finally {
      await session.close();
      expect(session.errors).toEqual([]);
    }
  } finally {
    await peer.close();
  }
});

for (const inputForm of ["request", "url"] as const) {
  test(`stale ${inputForm} read completes its body after caller abort without changing other reads`, async ({
    browser,
  }) => {
    const peer = await canary();
    try {
      const session = await openDemoSession(browser, peer, "stale-navigation");
      try {
        const target = `${peer.origin}/api/v2/economics/repositories/1/1/sources?limit=20`;
        const read = session.page.evaluate(
          async ({ target, inputForm }) => {
            const controller = new AbortController();
            Reflect.set(window, "__abortStaleTest", () => controller.abort());
            const result = await (inputForm === "request"
              ? fetch(new Request(target, { signal: controller.signal }))
              : fetch(target, { signal: controller.signal }));
            const body: unknown = await result.json();
            return { aborted: controller.signal.aborted, status: result.status, body };
          },
          { target, inputForm },
        );
        const observed = read.then(
          (value) => ({ kind: "completed", value }),
          () => ({ kind: "failed" }),
        );
        await session.scenario.staleRequested;
        await session.page.evaluate(() => {
          const abort = Reflect.get(window, "__abortStaleTest") as () => void;
          abort();
        });
        const finished = session.context.waitForEvent("requestfinished", {
          predicate: (request) => request.url() === target,
          timeout: 5_000,
        });
        session.scenario.releaseStale();
        const [result] = await Promise.all([observed, finished]);
        expect(result).toMatchObject({
          kind: "completed",
          value: {
            aborted: true,
            status: 200,
            body: { installationId: 1, repositoryId: 1, items: [{}, {}] },
          },
        });
        expect(
          await session.page.evaluate(async () => {
            const controller = new AbortController();
            const read = fetch("/api/v2/economics/repositories/1/2/sources?limit=20", {
              signal: controller.signal,
            });
            controller.abort();
            return read.then(
              () => "completed",
              () => "aborted",
            );
          }),
        ).toBe("aborted");
      } finally {
        await session.close();
        expect(session.errors).toEqual([]);
      }
    } finally {
      await peer.close();
    }
  });
}

test("canary shutdown closes an accepted incomplete HTTP connection", async () => {
  const peer = await canary();
  const url = new URL(peer.origin);
  const accepted = new Promise<Socket>((resolve) => peer.server.once("connection", resolve));
  const socket = connect({ host: url.hostname, port: Number(url.port) });
  const socketErrors: NodeJS.ErrnoException[] = [];
  socket.on("error", (error) => socketErrors.push(error));
  try {
    await once(socket, "connect");
    const received = once(await accepted, "data");
    socket.write("GET /incomplete HTTP/1.1\r\nHost: localhost\r\n");
    socket.resume();
    await received;
    expect(peer.connections).toBe(1);
    const closed = new Promise<void>((resolve) => socket.once("close", () => resolve()));
    await peer.close();
    await closed;
    for (const error of socketErrors) expect(error.code).toBe("ECONNRESET");
    expect(peer.requests).toEqual([]);
  } finally {
    socket.destroy();
    await peer.close();
  }
});

test("asset redirects are rejected before the browser can follow an unobserved hop", async ({
  browser,
  demoServer,
}) => {
  const peer = await canary();
  const redirector = await canary(`${peer.origin}/escaped`);
  const context = await browser.newContext({
    serviceWorkers: "block",
    proxy: { server: demoServer.origin, bypass: "<-loopback>" },
  });
  const scenario = createScenario("populated");
  const errors: unknown[] = [];
  try {
    const network = await installSyntheticNetwork(context, redirector.origin, scenario, (error) =>
      errors.push(error),
    );
    const page = await context.newPage();
    const rejected = context.waitForEvent(
      "requestfailed",
      (request) => request.url() === `${redirector.origin}/src/redirect.js`,
    );
    await page.goto(`${redirector.origin}/workbench`);
    await rejected;
    expect(redirector.requests).toContain("/src/redirect.js");
    expect(network.blocked).toContain("asset-status");
    expect(peer.requests).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    scenario.dispose();
    await context.close();
    await redirector.close();
    await peer.close();
  }
});

# Typed Promise Analysis

Status: accepted

Date: 2026-09-20

## Scope And Authority

This decision extends only the compatibility-API and retirement clauses of
[TypeScript Toolchain](typescript-toolchain.md). Native TypeScript 7.0.2 remains
the compiler, build and editor typechecking authority. Biome remains the
formatter and general linter. No runtime dependency or product policy changes.

## Decision

Use ESLint 10.11.0 with typescript-eslint 8.70.0 for one rule:
`@typescript-eslint/no-floating-promises`. Its project service reuses the
existing TypeScript 6 compatibility distribution and repository tsconfig
references. This API is now admitted for static Promise analysis as well as
OpenAPI generation; it does not replace native compilation. The upstream peer
contract admits the installed compatibility version, not TypeScript 7's native
package. Unsupported API versions must fail rather than suppress the warning.

Apply the rule to authored TypeScript in `src`, `tests` and `dev`, excluding
the generated API projection. Inspect Promise-like values as well as Promise.
An explicit `void` remains an intentional-detachment signal, not proof of
supervision: review still owns rejection handling, cancellation and lifetime.
Do not use `void act(...)` to hide an update that the test must await.

The same config serves ESLint-capable editors and `pnpm frontend:lint` before
publication. The existing mandatory `frontend:check` CI command invokes it;
no new workflow or machine-global Git hook is needed. VS Code recommendations
and the optional Dev Container enable discovery without replacing formatters.

## Rationale And Counterexamples

`dispatchEvent` returns boolean. Returning it from an `act` callback selects the
Promise-returning overload, so its result needs ownership. Native compilation
accepts an ignored result. In a bounded Biome 2.5.11 probe, its optional Promise
rule caught a direct async call but missed imported `act`; merely enabling that
rule did not close this counterexample. Reimplementing overload resolution or
moving all formatting to ESLint would add unnecessary policy and maintenance.

The admitted static fixture corpus must reject imported React/socket promises,
ordinary promises, thenables and fulfillment-only handlers; it must accept
await, return, joins, rejection handlers and documented synchronous/void cases.
Require the exact rule and error severity, not just a nonzero exit that might
come from parser failure. Re-run this corpus with dependency/config changes.

Five original visibility-event call sites are repaired using awaited async
`act`. Their pending mock responses remain explicitly unresolved until the
existing test resolves them. The synthetic WebSocket route returns its close
Promise and reports rejection to its existing error owner. Native tests must
preserve polling, duplicate-event and network-isolation observations.

## Boundaries And Revision

The qualifier analyzes a fixed corpus without executing its code. Local lint
does not prove scheduling correctness or all async lifetimes; native behavioral
qualification remains separate. Lint startup cost is additional, not claimed
free. Reassess if measured cost exceeds its feedback benefit or an existing
admitted tool detects the complete corpus more cheaply.

Retire the compatibility distribution only when both OpenAPI generation and
the Promise analyzer support its replacement with unchanged required results.
This extends the older OpenAPI-only retirement condition; no second compiler
or hand-maintained general type system is introduced.

References: [typed linting](https://typescript-eslint.io/getting-started/typed-linting/),
[supported dependencies](https://typescript-eslint.io/users/dependency-versions/),
[Promise rule](https://typescript-eslint.io/rules/no-floating-promises/).

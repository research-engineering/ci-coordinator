# TypeScript Toolchain

Status: accepted

Date: 2026-07-21

## Decision

Frontend compilation and editor analysis use native TypeScript 7.0.2. The
workspace exposes the native package as `@typescript/native`, whose `tsc`
executable owns every repository typecheck and production build.

`openapi-typescript` 7.13.0 still requires the programmatic `typescript` API.
TypeScript 7.0 does not expose that API, so the `typescript` dependency points
to Microsoft's `@typescript/typescript6` 6.0.2 compatibility distribution. This
package exposes `tsc6`, not `tsc`; it cannot silently replace the native
compiler. Its API authority is limited to deterministic OpenAPI projection.

## Selection Law

Let `C` be the compiler used by repository scripts, `A` the API consumed by a
generator, and `B(x)` the observable output of operation `x`.

```text
Admissible(C, A) :=
  C.version = 7.0.2
  and C.executable = tsc
  and A.distributionVersion = 6.0.2
  and A.executable != tsc
  and B(OpenApiProjection(A)) is reproducible
  and B(FrontendBuild(C)) satisfies the frontend contract
```

A single TypeScript 7 package is impossible in 7.0 because the required API is
absent. Keeping TypeScript 5 as the compiler would violate the native-compiler
requirement. The official side-by-side package is therefore the minimum
dependency set that satisfies both constraints without ambiguous executable
ownership.

## Static Contract

The frontend configuration explicitly owns the non-default checks that follow
from the build topology:

- `verbatimModuleSyntax` preserves ESM import intent;
- `isolatedModules` proves compatibility with Vite's per-file transform;
- `erasableSyntaxOnly` excludes TypeScript runtime constructs that require a
  compiler-specific transform;
- `noUncheckedSideEffectImports` rejects unresolved asset imports;
- `vite/client` admits the supported CSS import contract;
- `noImplicitReturns` rejects partial control-flow results;
- `noPropertyAccessFromIndexSignature` preserves declared-key intent; and
- explicit `rootDir`, runtime libraries, and test-framework type entrypoints
  prevent ambient or layout-dependent changes.

Native TypeScript 7 stable type ordering is mandatory and recorded explicitly.
Third-party declarations remain inside the proof surface rather than being
hidden by `skipLibCheck`. Vite remains the production emitter, while
`tsc --build` owns static proof.

## Editor Boundary

VS Code development containers use Microsoft's TypeScript native extension,
enable the native language service explicitly, and point it at the exact
workspace `@typescript/native` package. This prevents the `typescript` alias
reserved for the compatibility API from becoming editor authority.
Other editors should use their native TypeScript 7 LSP integration and select
the workspace 7.0.2 package where the editor supports an explicit toolchain
path. The community `typescript-language-server`, which adapts the legacy
`tsserver` protocol, is not part of the repository toolchain.

## Retirement Condition

Remove `@typescript/typescript6` and the corresponding peer compatibility rule
when a stable `openapi-typescript` release supports the TypeScript 7
programmatic API and the generated contract remains byte-identical under the
repository witness. The native compiler dependency remains.

## Non-Claims

This decision does not require application code to use every available syntax
feature. A language feature is admitted only when it reduces code or
strengthens an owned contract. Local typecheck and build evidence do not prove
browser compatibility, deployment, or provider behavior.

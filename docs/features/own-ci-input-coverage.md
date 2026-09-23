# Own-CI Input Coverage

Owner: repository tooling. This implements the bounded CF01/CF03 portion of
the external dynamic-CI blueprint, frozen at SHA-256
`a5c4d775207a6de30ab392bbd7ed9c70c427f596a5d5c849889dd6e83d05556f`.
That proposal is not repository authority or evidence of selective execution.
The execution sequence is in the [plan](own-ci-input-coverage-plan.md).

## Decision

Retain the existing `workflow.lint` command, pinned actionlint image, zizmor
and independent Full Check. Run actionlint over the explicit union of the
repository and two generated consumer workflow directories, and run the image's
existing ShellCheck over the two authored shell entrypoints using their actual
shell dialects. A build-only compatibility stage supplies the provider context
types described below; no product runtime dependency, scheduler or workflow
job is added. The final image digest pins ShellCheck and pyflakes; the adapter
source and Go lock bind the replacement actionlint binary. Native execution
must verify their availability.
The upstream [image definition](https://github.com/rhysd/actionlint/blob/v1.7.12/Dockerfile)
includes ShellCheck; [usage](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/usage.md)
distinguishes default repository discovery from explicit file arguments.

As-is, actionlint's default discovery checks the repository workflows but does
not establish coverage of the consumer fixtures; zizmor explicitly includes
those fixtures. Inline shell analysis does not inspect the standalone scripts.
The intended delta is additional input coverage, not changed gate semantics.

Let W be the union of direct `.yml` and `.yaml` regular files in the three
owned workflow directories. Every directory must exist and contribute a file.
The actionlint argv contains exactly sorted W, after options and `--`. A missing
directory, empty population or symlink is rejected before any tool execution.
The shell population S is the two explicit source/dialect pairs. A native
inventory witness examines tracked paths for shell-family suffixes and regular
files' bounded shell shebangs, including extensionless and env-launched sources.
It rejects discovered sources outside S, requiring an owner to extend the
profile. This is explicit deterministic detection, not a claim to infer the
language of arbitrary data files without a suffix, shebang or declared owner.
Other symlink targets are not traversed for language detection.

```text
LintSuccess => ActionlintPassed(W) and ShellCheckPassed(S) and ZizmorPassed
MissingInput or ToolFailure or AnyNonzeroExit => not LintSuccess
```

Each container reads the repository through a read-only mount, has no network,
uses a read-only root and drops Linux capabilities. These invocation settings
are not proof of an OS sandbox. Image acquisition remains a separate permitted
CI build operation. A shell source is parsed, never sourced or executed.

## Alternatives And Boundaries

Adding another ShellCheck image duplicates the already pinned payload.
Installing host binaries adds toolchain variation. A second workflow duplicates
setup and weakens the single command's coverage ownership. The selected route
adds two short container invocations, input admission and the compatibility
build stage described below. Revisit if native
cost measurements or unsupported fixture semantics defeat this choice.

Preserve the exact narrow actionlint false-positive exception for the admitted
`$/` trusted requester; do not suppress general reusable-workflow errors.
No Makefile is added, no custom YAML schema framework is introduced, and no
claim is made that these analyzers prove authorization, runtime shell behavior,
all blueprint obligations, complete security or production readiness.

## GitHub Context Compatibility

Native Full Check exposed a separate compatibility premise: the latest observed
actionlint release, 1.7.12, does not type the four GitHub.com `job.workflow_*`
string properties documented by the provider. The unchanged positive consumer
fixtures therefore fail analysis. They must not be rewritten or excluded.

Use actionlint's public `BuiltinGlobalVariableTypes` and `Command` APIs through
a small build-only Go entrypoint. Add exactly the four documented strings to
the otherwise strict job object before invoking the unmodified command. This
preserves unknown-property, wrong-type and context-availability errors, unlike
an error-message ignore. Fail if upstream already defines a field: a future
upgrade must explicitly retire this extension. There is no fork of the parser,
diagnostic postfilter, source preprocessing or new runtime service.

This revises the initial no-additional-build-stage preference because native
compatibility disproved its sufficiency. The cost is one digest-pinned Go
compiler stage and a small go.mod/go.sum closure for the same actionlint library;
the final image and its ShellCheck/pyflakes remain the existing pinned image.
The compiler is neither a backend dependency nor a required developer host tool.
CI builds with a read-only module graph, runs independent positive/typo/type/
unavailable-context cases, then copies only the resulting binary. Reconsider
and remove the extension when upstream supports the provider fields.

Provider authority: [GitHub job context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
Library authority: [public context types](https://github.com/rhysd/actionlint/blob/v1.7.12/expr_sema.go).
This admission is GitHub.com-specific, not a claim of GHES support.

## Writer Readiness

| Owner                | Delta and independent operands                                                            | Protected observations                                              | Sensitive gate / independent validator                                                                                 |
|----------------------|-------------------------------------------------------------------------------------------|---------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| Workflow tooling     | Directory population, file kind, exact argv, image, shell dialect, exit status            | Existing command ID, zizmor inputs, Full Check and narrow exception | Input omission/symlink/empty cases, exact source inventory and native workflow lint; batch reviewer                    |
| Proof routing        | Existing workflow command's shell input closure                                           | Existing requirements and native gate authority                     | Each standalone shell delta routes workflow lint; native selective-plan witnesses and Proofkit                         |
| Linter compatibility | Four provider-defined string fields, strict job type, CLI, unchanged context availability | Unchanged fixtures, other types/rules and final analyzer image      | Native valid fields, typo, string-member misuse, unavailable context and supersession guard; independent batch control |

Static inspection proves the current missing argv coverage. Native tools and
negative witnesses remain GitHub-only. Their expected results are independent
of the implementation's discovered file set and may not be generated from it.

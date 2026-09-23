from __future__ import annotations

import ast
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PythonSignals:
    first_party_import_contexts: int | None
    recognized_public_declarations: int | None


def signal_runtime_id() -> str:
    version = sys.version_info
    return f"{sys.implementation.name}-{version.major}.{version.minor}.{version.micro}"


def python_signals(path: str, source: str) -> PythonSignals:
    try:
        tree = ast.parse(source, filename=path, feature_version=(3, 13))
        return _parsed_python_signals(path, tree)
    except (RecursionError, SyntaxError, ValueError):
        return PythonSignals(
            first_party_import_contexts=None,
            recognized_public_declarations=None,
        )


def _parsed_python_signals(path: str, tree: ast.Module) -> PythonSignals:
    explicit_exports, declares_dunder_all = _static_dunder_all(tree)
    export_count = (
        None
        if declares_dunder_all and explicit_exports is None
        else len(explicit_exports)
        if explicit_exports is not None
        else sum(_public_declarations(statement) for statement in tree.body)
    )
    contexts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                context = _absolute_context(alias.name)
                if context is not None:
                    contexts.add(context)
        elif isinstance(node, ast.ImportFrom):
            context = (
                _relative_context(path, node.module, node.level)
                if node.level
                else _absolute_context(node.module or "")
            )
            if context is not None:
                contexts.add(context)
    return PythonSignals(
        first_party_import_contexts=len(contexts),
        recognized_public_declarations=export_count,
    )


def _static_dunder_all(tree: ast.Module) -> tuple[tuple[str, ...] | None, bool]:
    visitor = _ModuleScopeDunderAllVisitor()
    for statement in tree.body:
        visitor.visit(statement)
    if not visitor.occurrences:
        return None, False

    direct_assignments: list[tuple[ast.Name, ast.expr | None]] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                direct_assignments.append((target, statement.value))
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            if isinstance(target, ast.Name) and target.id == "__all__":
                direct_assignments.append((target, statement.value))

    if len(direct_assignments) != 1:
        return None, True
    target, value = direct_assignments[0]
    if (
        len(visitor.occurrences) != 1
        or visitor.occurrences[0] is not target
        or not isinstance(value, (ast.List, ast.Tuple))
    ):
        return None, True
    exports = tuple(
        element.value
        for element in value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    )
    return (
        (exports, True)
        if len(exports) == len(value.elts) and len(set(exports)) == len(exports)
        else (None, True)
    )


class _ModuleScopeDunderAllVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._class_control_depths: list[int] = []
        self._class_scope_states: list[str] = []
        self._dunder_all_shadow_depth = 0
        self.occurrences: list[ast.AST] = []

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != "__all__" or self._dunder_all_shadow_depth > 0:
            return
        if not self._class_scope_states:
            self.occurrences.append(node)
        elif isinstance(node.ctx, ast.Load):
            if self._class_scope_states[-1] != "bound":
                self.occurrences.append(node)
        else:
            self._class_scope_states[-1] = "unknown"

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._class_scope_states:
            self.generic_visit(node)
            return
        self.visit(node.value)
        for target in node.targets:
            self._visit_class_assignment_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self._class_scope_states:
            self.generic_visit(node)
            return
        self.visit(node.annotation)
        if node.value is None:
            if not isinstance(node.target, ast.Name):
                self.visit(node.target)
            return
        self.visit(node.value)
        self._visit_class_assignment_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if not self._class_scope_states:
            self.generic_visit(node)
            return
        self.visit(node.value)
        self._visit_class_assignment_target(node.target)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
            if bound_name == "__all__":
                self._record_binding(alias)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if (alias.asname or alias.name) == "__all__":
                self._record_binding(alias)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)
        self._class_scope_states.append("unbound")
        self._class_control_depths.append(0)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._class_control_depths.pop()
            self._class_scope_states.pop()
        if node.name == "__all__":
            self._record_binding(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name == "__all__":
            self._record_binding(node)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name == "__all__":
            self._record_binding(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name == "__all__":
            self._record_binding(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        for key in node.keys:
            self.visit(key)
        for pattern in node.patterns:
            self.visit(pattern)
        if node.rest == "__all__":
            self._record_binding(node)

    def visit_Global(self, node: ast.Global) -> None:
        if "__all__" in node.names:
            self.occurrences.append(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        if "__all__" in node.names:
            self.occurrences.append(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if (
            self._class_scope_states
            and self._dunder_all_shadow_depth == 0
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            if self._class_scope_states[-1] != "bound":
                self.occurrences.append(node.target)
            self.visit(node.value)
            self._bind_class_dunder_all()
            return
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self._visit_control_flow(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_control_flow(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_control_flow(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_control_flow(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_control_flow(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_control_flow(node)

    def visit_With(self, node: ast.With) -> None:
        self._visit_control_flow(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_control_flow(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_control_flow(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._visit_arguments(node.args)
        for expression in (
            *node.decorator_list,
            *((node.returns,) if node.returns is not None else ()),
            *getattr(node, "type_params", ()),
        ):
            self.visit(expression)
        if _contains_global_dunder_all_reference(node):
            self.occurrences.append(node)
        if node.name == "__all__":
            self._record_binding(node)

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg is not None else ()),
            *((arguments.kwarg,) if arguments.kwarg is not None else ()),
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for expression in (*arguments.defaults, *arguments.kw_defaults):
            if expression is not None:
                self.visit(expression)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        outputs: tuple[ast.expr, ...],
    ) -> None:
        initial_shadow_depth = self._dunder_all_shadow_depth
        try:
            for generator in generators:
                self.visit(generator.iter)
                if _target_binds_dunder_all(generator.target):
                    self._dunder_all_shadow_depth += 1
                for condition in generator.ifs:
                    self.visit(condition)
            for output in outputs:
                self.visit(output)
        finally:
            self._dunder_all_shadow_depth = initial_shadow_depth

    def _visit_class_assignment_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name) and target.id == "__all__":
            self._bind_class_dunder_all()
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            for element in target.elts:
                self._visit_class_assignment_target(element)
            return
        self.visit(target)

    def _record_binding(self, node: ast.AST) -> None:
        if self._class_scope_states:
            self._bind_class_dunder_all()
        else:
            self.occurrences.append(node)

    def _bind_class_dunder_all(self) -> None:
        if self._class_scope_states[-1] == "bound" or self._class_control_depths[-1] == 0:
            self._class_scope_states[-1] = "bound"
        else:
            self._class_scope_states[-1] = "unknown"

    def _visit_control_flow(self, node: ast.AST) -> None:
        if not self._class_scope_states:
            self.generic_visit(node)
            return
        self._class_control_depths[-1] += 1
        try:
            self.generic_visit(node)
        finally:
            self._class_control_depths[-1] -= 1


def _target_binds_dunder_all(target: ast.expr) -> bool:
    return any(isinstance(node, ast.Name) and node.id == "__all__" for node in ast.walk(target))


def _contains_global_dunder_all_reference(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    has_global_declaration = False
    has_reference = False
    for descendant in ast.walk(node):
        if isinstance(descendant, ast.Global) and "__all__" in descendant.names:
            has_global_declaration = True
        elif isinstance(descendant, ast.Name) and descendant.id == "__all__":
            has_reference = True
        if has_global_declaration and has_reference:
            return True
    return False


def _public_declarations(statement: ast.stmt) -> int:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return int(not statement.name.startswith("_"))
    if isinstance(statement, ast.Assign):
        return sum(
            isinstance(target, ast.Name) and not target.id.startswith("_")
            for target in statement.targets
        )
    if isinstance(statement, ast.AnnAssign):
        is_public_name = isinstance(
            statement.target, ast.Name
        ) and not statement.target.id.startswith("_")
        return int(is_public_name)
    return 0


def _absolute_context(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "ci_coordinator":
        return f"ci-coordinator.{parts[1]}"
    if parts and parts[0] == "scripts":
        return "repository-scripts"
    return None


def _relative_context(path: str, module: str | None, level: int) -> str | None:
    marker = "backend/src/ci_coordinator/"
    if not path.startswith(marker):
        return None
    package = path[len(marker) :].split("/")[:-1]
    retained = max(0, len(package) - (level - 1))
    resolved = [*package[:retained], *((module or "").split(".") if module else ())]
    return f"ci-coordinator.{resolved[0]}" if resolved and resolved[0] else None

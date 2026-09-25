from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass
from itertools import pairwise

_DYNAMIC_IMPORT_NAMES = frozenset(
    {
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "globals",
        "locals",
        "vars",
    }
)
_DYNAMIC_IMPORT_MEMBERS = frozenset(
    {
        "__builtins__",
        "__dict__",
        "__globals__",
        "__import__",
        "__loader__",
        "__spec__",
        "__subclasses__",
        "f_globals",
        "f_locals",
    }
)
_DYNAMIC_IMPORT_TOKENS = _DYNAMIC_IMPORT_NAMES | _DYNAMIC_IMPORT_MEMBERS
_MODULE_OBJECT_AUTHORITY_PREFIX = "reserved:module-object:"

type _AliasEnvironment = dict[str, frozenset[str]]
type _ScopeKey = tuple[str, str, int]
type _SymbolTableIndex = dict[_ScopeKey, list[symtable.SymbolTable]]


@dataclass(frozen=True, slots=True)
class _ImportBindingIndex:
    global_aliases: _AliasEnvironment
    function_aliases: dict[int, _AliasEnvironment]


@dataclass(frozen=True, slots=True)
class _FunctionBindingScope:
    local_names: frozenset[str]
    aliases: _AliasEnvironment


def _module_object_authority(module: str) -> str:
    return f"{_MODULE_OBJECT_AUTHORITY_PREFIX}{module}"


def scan_imported_modules(
    source: str,
    *,
    filename: str,
    current_package: str,
    ambient_authority_prefixes: tuple[str, ...],
    restricted_module_objects: tuple[str, ...],
    qualified_authority_prefixes: tuple[str, ...] = (),
) -> tuple[str, ...]:
    tree = ast.parse(source, filename=filename)
    root_symbols = symtable.symtable(source, filename, "exec")
    binding_index = _collect_import_bindings(tree, root_symbols, current_package)
    scanner = _ImportAuthorityScanner(
        current_package=current_package,
        ambient_authority_prefixes=ambient_authority_prefixes,
        restricted_module_objects=restricted_module_objects,
        qualified_authority_prefixes=qualified_authority_prefixes,
        symbol_tables=_symbol_table_index(root_symbols),
        binding_index=binding_index,
        postponed_annotations=_has_postponed_annotations(tree),
    )
    scanner.scan_module(tree, root_symbols)
    return tuple(sorted(scanner.imports))


def _from_import_base(node: ast.ImportFrom, current_package: str) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = current_package.split(".") if current_package else []
    keep = len(package_parts) - (node.level - 1)
    base_parts = package_parts[: max(keep, 0)]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(part for part in base_parts if part)


class _ImportAuthorityScanner(ast.NodeVisitor):
    """Collect a conservative, flow-insensitive syntactic authority projection."""

    def __init__(
        self,
        *,
        current_package: str,
        ambient_authority_prefixes: tuple[str, ...],
        restricted_module_objects: tuple[str, ...],
        qualified_authority_prefixes: tuple[str, ...],
        symbol_tables: _SymbolTableIndex,
        binding_index: _ImportBindingIndex,
        postponed_annotations: bool,
    ) -> None:
        self._current_package = current_package
        self._ambient_authority_prefixes = ambient_authority_prefixes
        self._qualified_authority_prefixes = qualified_authority_prefixes
        self._restricted_module_objects = frozenset(restricted_module_objects)
        self._symbol_tables = symbol_tables
        self._global_aliases = binding_index.global_aliases
        self._function_aliases = binding_index.function_aliases
        self._function_scopes: list[_FunctionBindingScope] = []
        self._postponed_annotations = postponed_annotations
        self._postponed_annotation_depth = 0
        self._aliases: _AliasEnvironment = {}
        self._function_parent_aliases: _AliasEnvironment = {}
        self.imports: set[str] = set()

    def scan_module(self, tree: ast.Module, symbols: symtable.SymbolTable) -> None:
        self._visit_scope(
            tuple(tree.body),
            inherited={},
            kind="module",
            symbols=symbols,
        )
        if self._symbol_tables:
            unresolved = ", ".join(
                f"{kind}:{name}:{line}x{len(tables)}"
                for (kind, name, line), tables in sorted(self._symbol_tables.items())
            )
            raise ValueError(f"unmapped Python lexical scopes: {unresolved}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name)
            self._record_restricted_module(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _from_import_base(node, self._current_package)
        for alias in node.names:
            if alias.name in _DYNAMIC_IMPORT_TOKENS:
                self.imports.add("reserved:dynamic-import")
            if alias.name == "*":
                self.imports.add("reserved:dynamic-import")
                continue
            authority = f"{base}.{alias.name}" if base else alias.name
            self.imports.add(authority)
            self._record_restricted_module(authority)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        if node.id in _DYNAMIC_IMPORT_NAMES:
            self.imports.add("reserved:dynamic-import")
            return
        if node.id in {"eval", "exec"}:
            self.imports.add("reserved:dynamic-execution")
            return
        authorities = self._resolve_name(node.id)
        self._record_references(authorities)
        self._record_restricted_references(authorities)
        self._record_bare_authority_roots(authorities)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if any(member in _DYNAMIC_IMPORT_MEMBERS for member in _attribute_members(node)):
            self.imports.add("reserved:dynamic-import")
        references = _resolved_references(node, self._aliases)
        if references is None:
            self.visit(node.value)
        else:
            self._record_references(references)
            self._record_restricted_references(references)
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Call):
                self.visit(base)

    def visit_Call(self, node: ast.Call) -> None:
        if _literal_getattr_member(node) in _DYNAMIC_IMPORT_TOKENS:
            self.imports.add("reserved:dynamic-import")
        literal_getattrs = _resolved_literal_getattrs(node, self._aliases)
        if literal_getattrs is None:
            self.visit(node.func)
            for expression in (
                *node.args,
                *(keyword.value for keyword in node.keywords),
            ):
                self.visit(expression)
            return
        self.visit(node.func)
        self._record_references(literal_getattrs)
        self._record_restricted_references(literal_getattrs)
        if not isinstance(node.args[0], ast.Name):
            self.visit(node.args[0])
        for expression in (
            *node.args[1:],
            *(keyword.value for keyword in node.keywords),
        ):
            self.visit(expression)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _literal_text(node.slice) in _DYNAMIC_IMPORT_TOKENS:
            self.imports.add("reserved:dynamic-import")
        self.visit(node.value)
        self.visit(node.slice)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_argument_expressions(node.args)
        if self._postponed_annotation_depth:
            return
        self._visit_scope(
            (node.body,),
            inherited=self._function_parent_aliases,
            kind="function",
            symbols=self._take_symbol_table("function", "lambda", node.lineno),
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in node.decorator_list:
            self.visit(expression)
        type_parameter_names = _type_parameter_names(node.type_params)
        self._visit_type_parameter_scope(
            node.type_params,
            (*node.bases, *(keyword.value for keyword in node.keywords)),
        )
        self._visit_scope(
            tuple(node.body),
            inherited=self._function_parent_aliases,
            kind="class",
            parameters=type_parameter_names,
            symbols=self._take_symbol_table("class", node.name, node.lineno),
        )

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        self._visit_type_parameter_scope(node.type_params, (node.value,))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        self._visit_annotation(node.annotation)
        if node.value is not None:
            self.visit(node.value)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        type_parameter_names = _type_parameter_names(node.type_params)
        self._visit_argument_defaults(node.args)
        annotation_expressions: tuple[ast.expr, ...] = (
            *_argument_annotations(node.args),
            *((node.returns,) if node.returns is not None else ()),
        )
        self._visit_type_parameter_scope(
            node.type_params,
            annotation_expressions,
            expressions_are_annotations=True,
        )
        self._visit_scope(
            tuple(node.body),
            inherited=self._function_parent_aliases,
            kind="function",
            parameters=type_parameter_names,
            symbols=self._take_symbol_table("function", node.name, node.lineno),
        )

    def _visit_type_parameter_scope(
        self,
        type_parameters: list[ast.type_param],
        expressions: tuple[ast.AST, ...],
        *,
        expressions_are_annotations: bool = False,
    ) -> None:
        names = _type_parameter_names(type_parameters)
        if not names:
            for expression in expressions:
                if expressions_are_annotations:
                    self._visit_annotation(expression)
                else:
                    self.visit(expression)
            return
        aliases = _masked_aliases(self._aliases, names)
        outer_aliases = self._aliases
        outer_function_parent = self._function_parent_aliases
        self._aliases = aliases
        self._function_parent_aliases = aliases
        try:
            for parameter in type_parameters:
                self.visit(parameter)
            for expression in expressions:
                if expressions_are_annotations:
                    self._visit_annotation(expression)
                else:
                    self.visit(expression)
        finally:
            self._aliases = outer_aliases
            self._function_parent_aliases = outer_function_parent

    def _visit_argument_expressions(self, arguments: ast.arguments) -> None:
        self._visit_argument_defaults(arguments)
        for annotation in _argument_annotations(arguments):
            self._visit_annotation(annotation)

    def _visit_argument_defaults(self, arguments: ast.arguments) -> None:
        for expression in (*arguments.defaults, *arguments.kw_defaults):
            if expression is not None:
                self.visit(expression)

    def _visit_annotation(self, annotation: ast.AST) -> None:
        if not self._postponed_annotations:
            self.visit(annotation)
            return
        self._postponed_annotation_depth += 1
        try:
            self.visit(annotation)
        finally:
            self._postponed_annotation_depth -= 1

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.expr, ...],
    ) -> None:
        if not generators:
            return
        self.visit(generators[0].iter)
        local_names = {name for generator in generators for name in _target_names(generator.target)}
        nodes: list[ast.AST] = []
        for index, generator in enumerate(generators):
            if index:
                nodes.append(generator.iter)
            nodes.extend(generator.ifs)
        nodes.extend(results)
        self._visit_scope(
            tuple(nodes),
            inherited=self._function_parent_aliases,
            kind="function",
            parameters=frozenset(local_names),
        )

    def _visit_scope(
        self,
        nodes: tuple[ast.AST, ...],
        *,
        inherited: _AliasEnvironment,
        kind: str,
        parameters: frozenset[str] = frozenset(),
        symbols: symtable.SymbolTable | None = None,
    ) -> None:
        imports = _collect_scope_imports(nodes, self._current_package)
        local_names = _local_symbol_names(symbols) if symbols is not None else frozenset()
        declared_global_names = _declared_global_names(symbols)
        lexical_inherited = inherited
        inherited = self._resolve_declared_parent_bindings(inherited, symbols)
        shadowed_names = (parameters - declared_global_names) | (
            local_names if kind != "class" else frozenset()
        )
        aliases = _masked_aliases(inherited, shadowed_names)
        for name, import_authorities in imports.items():
            _merge_aliases(aliases, name, import_authorities)
        if kind == "module":
            for name, binding_authorities in self._global_aliases.items():
                _merge_aliases(aliases, name, binding_authorities)
        elif kind == "function" and symbols is not None:
            for name, binding_authorities in self._function_aliases.get(
                symbols.get_id(), {}
            ).items():
                _merge_aliases(aliases, name, binding_authorities)

        outer_aliases = self._aliases
        outer_function_parent = self._function_parent_aliases
        function_scope: _FunctionBindingScope | None = None
        if kind == "function":
            function_scope = _FunctionBindingScope(
                local_names=parameters | local_names,
                aliases=aliases,
            )
            self._function_scopes.append(function_scope)
        self._aliases = aliases
        self._function_parent_aliases = (
            _masked_aliases(lexical_inherited, parameters) if kind == "class" else aliases
        )
        try:
            for node in nodes:
                self.visit(node)
        finally:
            if function_scope is not None:
                popped = self._function_scopes.pop()
                if popped is not function_scope:
                    raise RuntimeError("Python lexical function scope stack is corrupted")
            self._aliases = outer_aliases
            self._function_parent_aliases = outer_function_parent

    def _resolve_declared_parent_bindings(
        self,
        inherited: _AliasEnvironment,
        symbols: symtable.SymbolTable | None,
    ) -> _AliasEnvironment:
        resolved = {name: frozenset(authorities) for name, authorities in inherited.items()}
        if symbols is None:
            return resolved
        for name in symbols.get_identifiers():
            symbol = symbols.lookup(name)
            if symbol.is_declared_global():
                resolved[name] = self._global_aliases.get(name, frozenset())
            elif symbol.is_nonlocal():
                resolved[name] = self._nearest_function_binding(name)
        return resolved

    def _nearest_function_binding(self, name: str) -> frozenset[str]:
        for scope in reversed(self._function_scopes):
            if name in scope.local_names:
                return scope.aliases.get(name, frozenset())
        raise ValueError(f"missing Python nonlocal binding owner: {name}")

    def _take_symbol_table(
        self,
        kind: str,
        name: str,
        line: int,
    ) -> symtable.SymbolTable:
        return _take_symbol_table(self._symbol_tables, kind, name, line)

    def _resolve_name(self, name: str) -> frozenset[str]:
        return self._aliases.get(name, frozenset({name}))

    def _record_restricted_module(self, authority: str) -> None:
        if authority in self._restricted_module_objects:
            self.imports.add(_module_object_authority(authority))

    def _record_restricted_references(self, authorities: frozenset[str]) -> None:
        for authority in authorities:
            self._record_restricted_module(authority)

    def _record_references(self, authorities: frozenset[str]) -> None:
        self.imports.update(
            authority
            for authority in authorities
            if _matches_import_prefix(
                authority,
                (*self._ambient_authority_prefixes, *self._qualified_authority_prefixes),
            )
        )
        for authority in authorities:
            parts = authority.split(".")
            if ("sys", "modules") in pairwise(parts):
                self.imports.add("sys.modules")

    def _record_bare_authority_roots(self, authorities: frozenset[str]) -> None:
        for authority in authorities:
            self.imports.update(
                prefix
                for prefix in self._ambient_authority_prefixes
                if prefix.startswith(f"{authority}.")
            )


class _ScopeImportCollector(ast.NodeVisitor):
    def __init__(self, current_package: str) -> None:
        self._current_package = current_package
        self.imports: dict[str, set[str]] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.partition(".")[0]
            authority = alias.name if alias.asname else bound_name
            self._record_import(bound_name, authority)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _from_import_base(node, self._current_package)
        for alias in node.names:
            if alias.name == "*":
                continue
            authority = f"{base}.{alias.name}" if base else alias.name
            self._record_import(alias.asname or alias.name, authority)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def _record_import(self, name: str, authority: str) -> None:
        self.imports.setdefault(name, set()).add(authority)


def _collect_scope_imports(
    nodes: tuple[ast.AST, ...],
    current_package: str,
) -> dict[str, set[str]]:
    collector = _ScopeImportCollector(current_package)
    for node in nodes:
        collector.visit(node)
    return collector.imports


class _ImportBindingCollector(ast.NodeVisitor):
    def __init__(
        self,
        current_package: str,
        symbol_tables: _SymbolTableIndex,
    ) -> None:
        self._current_package = current_package
        self._symbol_tables = symbol_tables
        self._function_scopes: list[symtable.SymbolTable] = []
        self.global_aliases: _AliasEnvironment = {}
        self.function_aliases: dict[int, _AliasEnvironment] = {}

    def collect(self, tree: ast.Module, symbols: symtable.SymbolTable) -> _ImportBindingIndex:
        self._collect_scope(tuple(tree.body), symbols, kind="module")
        return _ImportBindingIndex(
            global_aliases=self.global_aliases,
            function_aliases=self.function_aliases,
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbols = _take_symbol_table(self._symbol_tables, "class", node.name, node.lineno)
        self._collect_scope(tuple(node.body), symbols, kind="class")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        symbols = _take_symbol_table(self._symbol_tables, "function", node.name, node.lineno)
        self._collect_scope(tuple(node.body), symbols, kind="function")

    def _collect_scope(
        self,
        nodes: tuple[ast.AST, ...],
        symbols: symtable.SymbolTable,
        *,
        kind: str,
    ) -> None:
        imports = _collect_scope_imports(nodes, self._current_package)
        for name, authorities in imports.items():
            symbol = symbols.lookup(name)
            if kind == "module" or symbol.is_declared_global():
                _merge_aliases(self.global_aliases, name, authorities)
            elif symbol.is_nonlocal():
                owner = self._nonlocal_owner(name)
                target = self.function_aliases.setdefault(owner.get_id(), {})
                _merge_aliases(target, name, authorities)
            elif kind == "function":
                target = self.function_aliases.setdefault(symbols.get_id(), {})
                _merge_aliases(target, name, authorities)

        pushed = kind == "function"
        if pushed:
            self._function_scopes.append(symbols)
        try:
            for node in nodes:
                self.visit(node)
        finally:
            if pushed:
                popped = self._function_scopes.pop()
                if popped is not symbols:
                    raise RuntimeError("Python import binding scope stack is corrupted")

    def _nonlocal_owner(self, name: str) -> symtable.SymbolTable:
        for symbols in reversed(self._function_scopes):
            if name in symbols.get_identifiers() and symbols.lookup(name).is_local():
                return symbols
        raise ValueError(f"missing Python nonlocal binding owner: {name}")


def _collect_import_bindings(
    tree: ast.Module,
    root_symbols: symtable.SymbolTable,
    current_package: str,
) -> _ImportBindingIndex:
    return _ImportBindingCollector(
        current_package,
        _symbol_table_index(root_symbols),
    ).collect(tree, root_symbols)


def _merge_aliases(
    aliases: _AliasEnvironment,
    name: str,
    authorities: set[str] | frozenset[str],
) -> None:
    aliases[name] = aliases.get(name, frozenset()) | frozenset(authorities)


def _masked_aliases(
    aliases: _AliasEnvironment,
    names: frozenset[str],
) -> _AliasEnvironment:
    masked = {name: frozenset(authorities) for name, authorities in aliases.items()}
    for name in names:
        masked[name] = frozenset()
    return masked


def _argument_annotations(arguments: ast.arguments) -> tuple[ast.expr, ...]:
    positional = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    )
    optional = tuple(
        argument for argument in (arguments.vararg, arguments.kwarg) if argument is not None
    )
    return tuple(
        argument.annotation
        for argument in (*positional, *optional)
        if argument.annotation is not None
    )


def _has_postponed_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _local_symbol_names(symbols: symtable.SymbolTable) -> frozenset[str]:
    return frozenset(name for name in symbols.get_identifiers() if symbols.lookup(name).is_local())


def _declared_global_names(symbols: symtable.SymbolTable | None) -> frozenset[str]:
    if symbols is None:
        return frozenset()
    return frozenset(
        name for name in symbols.get_identifiers() if symbols.lookup(name).is_declared_global()
    )


def _symbol_table_index(root: symtable.SymbolTable) -> _SymbolTableIndex:
    index: _SymbolTableIndex = {}

    def visit(symbols: symtable.SymbolTable) -> None:
        for child in symbols.get_children():
            kind = child.get_type().value
            name = child.get_name()
            if kind == "class" or (
                kind == "function" and name not in {"dictcomp", "genexpr", "listcomp", "setcomp"}
            ):
                index.setdefault((kind, name, child.get_lineno()), []).append(child)
            visit(child)

    visit(root)
    return index


def _take_symbol_table(
    index: _SymbolTableIndex,
    kind: str,
    name: str,
    line: int,
) -> symtable.SymbolTable:
    key = (kind, name, line)
    tables = index.get(key)
    if not tables:
        raise ValueError(f"missing Python lexical scope: {kind}:{name}:{line}")
    table = tables.pop(0)
    if not tables:
        del index[key]
    return table


def _type_parameter_names(parameters: list[ast.type_param]) -> frozenset[str]:
    names: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple):
            raise TypeError(f"unsupported Python type parameter: {type(parameter).__name__}")
        names.add(parameter.name)
    return frozenset(names)


def _target_names(target: ast.expr) -> frozenset[str]:
    return frozenset(
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )


def _attribute_members(node: ast.Attribute) -> tuple[str, ...]:
    members: list[str] = []
    cursor: ast.expr = node
    while isinstance(cursor, ast.Attribute):
        members.append(cursor.attr)
        cursor = cursor.value
    return tuple(reversed(members))


def _resolved_references(
    node: ast.expr,
    aliases: _AliasEnvironment,
) -> frozenset[str] | None:
    attributes: list[str] = []
    cursor: ast.expr = node
    while True:
        if isinstance(cursor, ast.Attribute):
            attributes.append(cursor.attr)
            cursor = cursor.value
        elif isinstance(cursor, ast.Call) and (member := _literal_getattr_member(cursor)):
            attributes.append(member)
            cursor = cursor.args[0]
        else:
            break
    if not isinstance(cursor, ast.Name):
        return None
    roots = aliases.get(cursor.id, frozenset({cursor.id}))
    suffix = tuple(reversed(attributes))
    return frozenset(".".join((root, *suffix)) for root in roots)


def _resolved_literal_getattrs(
    node: ast.Call,
    aliases: _AliasEnvironment,
) -> frozenset[str] | None:
    if _literal_getattr_member(node) is None:
        return None
    return _resolved_references(node, aliases)


def _literal_getattr_member(node: ast.Call) -> str | None:
    if (
        not isinstance(node.func, ast.Name)
        or node.func.id != "getattr"
        or len(node.args) not in {2, 3}
    ):
        return None
    member = _literal_text(node.args[1])
    return member if member is not None and member.isidentifier() else None


def _literal_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and type(value.value) is str
        ]
        return "".join(parts) if len(parts) == len(node.values) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        right = _literal_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _matches_import_prefix(imported: str, prefixes: tuple[str, ...]) -> bool:
    return any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in prefixes)

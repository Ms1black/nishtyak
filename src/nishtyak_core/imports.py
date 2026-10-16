from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

from .models import Finding, ImportObservation, SourceFile


@dataclass(frozen=True)
class ParsedSource:
    imports: tuple[ImportObservation, ...]
    findings: tuple[Finding, ...]
    has_main_guard: bool = False
    complete: bool = True
    has_top_level_code: bool = False


class ImportScanner(ast.NodeVisitor):
    """Read imports and their execution context without importing project modules."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.observations: list[ImportObservation] = []
        self.findings: list[Finding] = []
        self.context = "runtime"
        self.importlib_aliases = {"importlib"}
        self.dynamic_functions = {"__import__"}
        self.import_module_functions: set[str] = set()
        self.typing_aliases = {"typing"}
        self.type_guards = {"TYPE_CHECKING"}
        self.has_main_guard = False
        self._scope_depth = 0

    @classmethod
    def scan(cls, source: SourceFile) -> ParsedSource:
        scanner = cls(source.path)
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(source.content).readline)
            tree = ast.parse(source.content.decode(encoding), filename=source.path)
            scanner.visit(tree)
            has_top_level_code = any(
                not isinstance(
                    statement,
                    (
                        ast.Import,
                        ast.ImportFrom,
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                        ast.Pass,
                    ),
                )
                and not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
                for statement in tree.body
            )
        except (SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
            return ParsedSource(
                (),
                (Finding("SOURCE_PARSE_ERROR", "Python source could not be parsed.", source.path),),
                complete=False,
            )
        return ParsedSource(
            tuple(scanner.observations),
            tuple(scanner.findings),
            scanner.has_main_guard,
            has_top_level_code=has_top_level_code,
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.observations.append(
                ImportObservation(alias.name, self.path, node.lineno, context=self.context)
            )
            if alias.name == "importlib":
                self.importlib_aliases.add(alias.asname or alias.name)
            if alias.name == "typing":
                self.typing_aliases.add(alias.asname or alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.observations.append(
            ImportObservation(
                node.module or "",
                self.path,
                node.lineno,
                tuple(alias.name for alias in node.names),
                node.level,
                self.context,
                "from",
            )
        )
        if node.level == 0 and node.module == "importlib":
            aliases = {
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            }
            self.dynamic_functions.update(aliases)
            self.import_module_functions.update(aliases)
        if node.level == 0 and node.module == "typing":
            self.type_guards.update(
                alias.asname or alias.name for alias in node.names if alias.name == "TYPE_CHECKING"
            )

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._scope_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._scope_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self.has_main_guard = self.has_main_guard or (
            self._scope_depth == 0 and self._is_main_guard(node.test)
        )
        self.visit(node.test)
        if self._is_type_guard(node.test):
            self._visit_body(node.body, "type_checking")
        else:
            for statement in node.body:
                self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Try(self, node: ast.Try) -> None:
        optional = any(self._catches_import_error(handler.type) for handler in node.handlers)
        self._visit_body(node.body, "optional" if optional else self.context)
        for handler in node.handlers:
            self.visit(handler)
        for statement in (*node.orelse, *node.finalbody):
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        dynamic = isinstance(function, ast.Name) and function.id in self.dynamic_functions
        dynamic = dynamic or (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id in self.importlib_aliases
        )
        if dynamic:
            module = (
                node.args[0]
                if node.args
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "name"), None
                )
            )
            observation = None
            if isinstance(module, ast.Constant) and isinstance(module.value, str):
                name = module.value
                level = len(name) - len(name.lstrip("."))
                is_import_module = (
                    isinstance(function, ast.Name) and function.id in self.import_module_functions
                ) or isinstance(function, ast.Attribute)
                if level and is_import_module:
                    package = (
                        node.args[1]
                        if len(node.args) > 1
                        else next(
                            (
                                keyword.value
                                for keyword in node.keywords
                                if keyword.arg == "package"
                            ),
                            None,
                        )
                    )
                    if isinstance(package, ast.Constant) and isinstance(package.value, str):
                        parts = package.value.split(".")
                        if level <= len(parts) and all(part.isidentifier() for part in parts):
                            prefix = parts[: len(parts) - level + 1]
                            suffix = name.lstrip(".").split(".") if name.lstrip(".") else []
                            name = ".".join((*prefix, *suffix))
                            level = 0
                    # Relative import_module requires its explicit package argument;
                    # the importing source's package is never an implicit substitute.
                    if level:
                        name = ""
                elif level:
                    # __import__ uses the separate level parameter, not leading dots.
                    name = ""
                if isinstance(function, ast.Name) and function.id == "__import__":
                    explicit_level = (
                        node.args[4]
                        if len(node.args) > 4
                        else next(
                            (keyword.value for keyword in node.keywords if keyword.arg == "level"),
                            None,
                        )
                    )
                    if explicit_level is not None and not (
                        isinstance(explicit_level, ast.Constant) and explicit_level.value == 0
                    ):
                        name = ""
                if name and all(part.isidentifier() for part in name.split(".")):
                    observation = ImportObservation(
                        name,
                        self.path,
                        node.lineno,
                        context=self.context,
                        kind="dynamic",
                    )
                    self.observations.append(observation)
            self.findings.append(
                Finding(
                    "DYNAMIC_IMPORT",
                    "Dynamic import found; computed imports may require manual dependencies.",
                    self.path,
                    node.lineno,
                    evidence_ids=(observation.id,) if observation else (),
                )
            )
            if observation is None:
                self.findings.append(
                    Finding(
                        "DYNAMIC_IMPORT_UNRESOLVED",
                        "The dynamic import target or package context is not statically known.",
                        self.path,
                        node.lineno,
                    )
                )
        self.generic_visit(node)

    def _visit_body(self, body: list[ast.stmt], context: str) -> None:
        previous = self.context
        self.context = "type_checking" if previous == "type_checking" else context
        try:
            for statement in body:
                self.visit(statement)
        finally:
            self.context = previous

    def _is_type_guard(self, node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id in self.type_guards) or (
            isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id in self.typing_aliases
        )

    @staticmethod
    def _catches_import_error(node: ast.expr | None) -> bool:
        if isinstance(node, ast.Name):
            return node.id in {"ImportError", "ModuleNotFoundError"}
        return isinstance(node, ast.Tuple) and any(
            ImportScanner._catches_import_error(item) for item in node.elts
        )

    @staticmethod
    def _is_main_guard(node: ast.expr) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return False
        if not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
            return False
        left, right = node.left, node.comparators[0]
        return (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ) or (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        )

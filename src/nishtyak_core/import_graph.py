from __future__ import annotations

import ast
import posixpath
from collections import deque
from dataclasses import asdict, dataclass

from .models import ImportObservation, ProjectSnapshot


@dataclass(frozen=True)
class LocalResolution:
    module: str
    status: str
    paths: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    reason: str = ""
    missing_names: tuple[str, ...] = ()
    script_context: bool = False

    @property
    def found(self) -> bool:
        return self.status == "local"


class LocalModuleIndex:
    """Resolve snapshot paths using ordered Python import search locations.

    No sys.path, find_spec, import hooks or project code are consulted. Regular
    packages and modules shadow later roots; namespace package portions merge.
    A script's sibling location is a conditional fallback, recorded as such.
    """

    def __init__(self, snapshot: ProjectSnapshot, source_roots: tuple[str, ...]) -> None:
        self.roots = tuple(dict.fromkeys(source_roots))
        self._files = {source.path: source for source in snapshot.files}
        self._directories: set[str] = {"."}
        self.file_modules: dict[str, str] = {}
        self.package_files = {
            source.path for source in snapshot.files if source.path.endswith("/__init__.py")
        }
        if "__init__.py" in self._files:
            self.package_files.add("__init__.py")
        self._symbols: dict[str, dict[str, set[int]]] = {}
        possible_modules: set[str] = set()
        for source in snapshot.files:
            directory = posixpath.dirname(source.path)
            while directory:
                self._directories.add(directory)
                directory = posixpath.dirname(directory)
            if not source.path.endswith(".py"):
                continue
            matches: list[tuple[int, str]] = []
            for root in self.roots:
                prefix = root + "/" if root != "." else ""
                if not source.path.startswith(prefix):
                    continue
                parts = source.path[len(prefix) : -3].split("/")
                if parts[-1] == "__init__":
                    parts.pop()
                if not parts or not all(part.isidentifier() for part in parts):
                    continue
                module = ".".join(parts)
                matches.append((len(prefix), module))
                possible_modules.update(
                    ".".join(parts[:length]) for length in range(1, len(parts) + 1)
                )
            if matches:
                # The most specific configured root gives a useful package context
                # for relative imports; absolute provider selection still obeys order.
                self.file_modules[source.path] = max(matches, key=lambda item: item[0])[1]
        self.modules = {module for module in possible_modules if self.resolve_module(module).found}

    def resolve_module(self, module: str, roots: tuple[str, ...] | None = None) -> LocalResolution:
        parts = module.split(".")
        if not module or not all(part.isidentifier() for part in parts):
            return LocalResolution(module, "external", reason="No valid static module name.")
        locations = self.roots if roots is None else roots
        providers: list[str] = []
        local_prefix = False
        for index, part in enumerate(parts):
            namespaces = []
            chosen_file = None
            chosen_package = None
            for location in locations:
                child = self._join(location, part)
                initializer = child + "/__init__.py"
                if initializer in self._files:
                    chosen_file, chosen_package = initializer, child
                    break
                module_file = child + ".py"
                if module_file in self._files:
                    chosen_file = module_file
                    break
                if child in self._directories:
                    namespaces.append(child)
            if chosen_file is not None:
                local_prefix = True
                providers.append(chosen_file)
                if chosen_package is None:
                    if index != len(parts) - 1:
                        return LocalResolution(
                            module,
                            "missing_local",
                            tuple(providers),
                            reason="A local module shadows this name but is not a package.",
                        )
                    return LocalResolution(module, "local", tuple(providers))
                locations = (chosen_package,)
            elif namespaces:
                local_prefix = True
                locations = tuple(dict.fromkeys(namespaces))
            else:
                return LocalResolution(
                    module,
                    "missing_local" if local_prefix else "external",
                    tuple(providers),
                    reason="A local package has no matching submodule."
                    if local_prefix
                    else "No matching provider in configured source roots.",
                )
        return LocalResolution(module, "local", tuple(providers), tuple(locations))

    def resolve_observation(self, observation: ImportObservation) -> LocalResolution:
        if observation.level:
            absolute = self._relative_name(observation)
            if absolute is None:
                return LocalResolution(
                    "." * observation.level + observation.module,
                    "missing_local",
                    reason="Relative import has no valid package context inside the project.",
                )
            base = self.resolve_module(absolute)
        else:
            absolute = observation.module
            base = self.resolve_module(absolute)
            parent = posixpath.dirname(observation.path) or "."
            if parent not in self.roots and not observation.path.endswith("/__init__.py"):
                script = self.resolve_module(absolute, (parent, *self.roots))
                if base.status == "external" and script.status != "external":
                    base = LocalResolution(
                        script.module,
                        script.status,
                        script.paths,
                        script.locations,
                        "Resolved with the importing script's directory on sys.path.",
                        script_context=True,
                    )
                elif base.found and script.found and base.paths != script.paths:
                    base = LocalResolution(
                        base.module,
                        base.status,
                        tuple(dict.fromkeys((*base.paths, *script.paths))),
                        base.locations,
                        "Script and module launch contexts select different local providers.",
                        script_context=True,
                    )
        if not base.found or observation.kind != "from":
            return base
        providers = list(base.paths)
        missing_names = []
        for name in observation.names:
            if name == "*":
                continue
            # A package attribute is checked before Python attempts a submodule.
            if base.paths and name in self.symbols(
                base.paths[-1],
                exclude_line=observation.line if base.paths[-1] == observation.path else None,
            ):
                continue
            child = self.resolve_module(name, base.locations) if base.locations else None
            if child is not None and child.found:
                providers.extend(child.paths)
            else:
                missing_names.append(name)
        return LocalResolution(
            base.module,
            base.status,
            tuple(dict.fromkeys(providers)),
            base.locations,
            base.reason,
            tuple(missing_names),
            base.script_context,
        )

    def resolve_relative(self, observation: ImportObservation) -> str | None:
        """Compatibility API: return an existing absolute relative-import base."""
        module = self._relative_name(observation)
        return module if module and self.resolve_module(module).found else None

    def symbols(self, path: str, *, exclude_line: int | None = None) -> frozenset[str]:
        if path not in self._symbols:
            source = self._files.get(path)
            bindings: dict[str, set[int]] = {}
            if source is not None and len(source.content) <= 1_000_000:
                try:
                    tree = ast.parse(source.content, filename=path)
                except (SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
                    tree = None
                if tree is not None:
                    collector = _ModuleSymbols()
                    collector.visit(tree)
                    bindings = collector.bindings
            self._symbols[path] = bindings
        return frozenset(
            name for name, lines in self._symbols[path].items() if lines - {exclude_line}
        )

    def _relative_name(self, observation: ImportObservation) -> str | None:
        current = self.file_modules.get(observation.path)
        if current is None:
            return None
        package = current.split(".")
        if observation.path not in self.package_files:
            package.pop()
        if observation.level > len(package):
            return None
        prefix = package[: len(package) - observation.level + 1]
        suffix = observation.module.split(".") if observation.module else []
        return ".".join((*prefix, *suffix))

    @staticmethod
    def _join(root: str, child: str) -> str:
        return child if root == "." else root + "/" + child


class _ModuleSymbols(ast.NodeVisitor):
    """Collect possible module bindings, excluding function and class bodies."""

    def __init__(self) -> None:
        self.bindings: dict[str, set[int]] = {}

    def _bind(self, name: str, line: int) -> None:
        self.bindings.setdefault(name, set()).add(line)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._bind(node.id, node.lineno)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._bind(node.name, node.lineno)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name, node.lineno)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name.split(".")[0], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._bind(alias.asname or alias.name, node.lineno)


@dataclass(frozen=True)
class ImportEdge:
    source_path: str
    module: str
    line: int
    evidence_id: str
    target_paths: tuple[str, ...]
    status: str
    reason: str = ""
    context: str = "runtime"
    missing_names: tuple[str, ...] = ()


class ImportGraph:
    """Full-project static graph with evidence and cycle-safe file reachability."""

    def __init__(
        self,
        snapshot: ProjectSnapshot,
        source_roots: tuple[str, ...],
        imports: tuple[ImportObservation, ...],
    ) -> None:
        self.index = LocalModuleIndex(snapshot, source_roots)
        self.nodes = tuple(
            sorted(source.path for source in snapshot.files if source.path.endswith(".py"))
        )
        edges = []
        adjacency: dict[str, set[str]] = {path: set() for path in self.nodes}
        for observation in imports:
            resolution = self.index.resolve_observation(observation)
            status = (
                "script_local"
                if resolution.found and resolution.script_context
                else resolution.status
            )
            if resolution.missing_names:
                status = "unverified_symbol"
            edge = ImportEdge(
                observation.path,
                resolution.module,
                observation.line,
                observation.id,
                resolution.paths,
                status,
                resolution.reason,
                observation.context,
                resolution.missing_names,
            )
            edges.append(edge)
            adjacency.setdefault(observation.path, set()).update(resolution.paths)
        self.edges = tuple(edges)
        self._adjacency = {source: tuple(sorted(targets)) for source, targets in adjacency.items()}

    def successors(self, source_path: str) -> tuple[str, ...]:
        return self._adjacency.get(source_path, ())

    def reachable(self, source_paths: tuple[str, ...]) -> tuple[str, ...]:
        remaining = deque(source_paths)
        visited = set()
        while remaining:
            current = remaining.popleft()
            if current in visited or current not in self._adjacency:
                continue
            visited.add(current)
            remaining.extend(self._adjacency[current])
        return tuple(sorted(visited))

    def to_dict(self) -> dict[str, object]:
        return {"nodes": list(self.nodes), "edges": [asdict(edge) for edge in self.edges]}

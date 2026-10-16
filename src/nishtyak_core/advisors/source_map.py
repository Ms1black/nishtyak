from __future__ import annotations

import ast
from typing import Any


class PythonSourceMap:
    """Small static observations, including definitions beyond the source preview."""

    max_nodes = 25_000

    def describe(self, content: bytes) -> dict[str, Any]:
        try:
            tree = ast.parse(content)
        except (SyntaxError, UnicodeError, LookupError, ValueError, RecursionError):
            return {"parsed": False, "symbols": [], "calls": [], "lookup_keys": []}
        symbols, calls, keys = [], [], []
        seen_calls: set[str] = set()
        seen_keys: set[str] = set()
        partial = False
        for index, node in enumerate(ast.walk(tree)):
            if index >= self.max_nodes:
                partial = True
                break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if len(symbols) >= 16:
                    partial = True
                    continue
                symbol: dict[str, Any] = {
                    "name": node.name[:120],
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                }
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbol["arguments"] = [
                        item.arg[:80]
                        for item in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                    ][:12]
                symbols.append(symbol)
            elif isinstance(node, ast.Call):
                name = self._name(node.func)
                if not name or name in seen_calls:
                    continue
                if len(calls) >= 24:
                    partial = True
                    continue
                seen_calls.add(name)
                call: dict[str, Any] = {"name": name, "line": node.lineno}
                # Literal arguments show observed filenames/grouping keys, never data contents.
                literals = [
                    item.value[:120]
                    for item in node.args[:4]
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
                if literals:
                    call["string_arguments"] = literals
                calls.append(call)
            elif isinstance(node, ast.Subscript):
                key = node.slice
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and len(key.value) <= 120
                    and key.value not in seen_keys
                ):
                    if len(keys) >= 20:
                        partial = True
                        continue
                    seen_keys.add(key.value)
                    keys.append({"key": key.value, "line": node.lineno})
        return {
            "parsed": True,
            "symbols": symbols,
            "calls": calls,
            "lookup_keys": keys,
            "partial": partial,
        }

    @staticmethod
    def _name(node: ast.AST) -> str:
        parts = []
        for _ in range(8):
            if isinstance(node, ast.Name):
                parts.append(node.id)
                return ".".join(reversed(parts))[:160]
            if isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            else:
                break
        return ""

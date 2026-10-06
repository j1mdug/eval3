"""Split test files by test function: the implementer sees some tests, the rest are run afterward.

Module-level code (imports, helpers, strategies) is kept in both halves so each half runs on its own.
"""

from __future__ import annotations

import ast
import random


def _is_test(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")


def _is_test_class(node: ast.AST) -> bool:
    return isinstance(node, ast.ClassDef) and node.name.startswith("Test")


def _segment(lines: list[str], node: ast.AST) -> str:
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    return "".join(lines[start : node.end_lineno])


def _test_ids(rel: str, tree: ast.Module) -> list[str]:
    ids = []
    for node in tree.body:
        if _is_test(node):
            ids.append(f"{rel}::{node.name}")
        elif _is_test_class(node):
            ids += [f"{rel}::{node.name}::{m.name}" for m in node.body if _is_test(m)]
    return ids


def _render(source: str, tree: ast.Module, rel: str, keep: set[str]) -> str:
    """Rebuild the module with all non-test code plus only the tests whose ids are in `keep`."""
    lines = source.splitlines(keepends=True)
    chunks = []
    for node in tree.body:
        if _is_test(node):
            if f"{rel}::{node.name}" in keep:
                chunks.append(_segment(lines, node))
        elif _is_test_class(node):
            kept = [m for m in node.body if _is_test(m) and f"{rel}::{node.name}::{m.name}" in keep]
            if kept:
                others = [c for c in node.body if not _is_test(c)]
                body = [_segment(lines, c).rstrip() + "\n" for c in others + kept]
                chunks.append(lines[node.lineno - 1] + "\n".join(body))
        else:
            chunks.append(_segment(lines, node))
    return "\n\n".join(c.rstrip() for c in chunks) + "\n"


def split(sources: dict[str, str], fraction: float, seed: int = 0) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return (visible sources, held-out sources, held-out test ids).

    A file only appears on a side if it has at least one test there, so neither half
    contains test-less modules that would fail at import before the feature exists.
    """
    trees = {rel: ast.parse(src) for rel, src in sources.items()}
    ids = {rel: _test_ids(rel, tree) for rel, tree in trees.items()}
    all_ids = [i for rel in sources for i in ids[rel]]
    k = min(len(all_ids) - 1, max(1, round(len(all_ids) * fraction))) if len(all_ids) >= 2 and fraction > 0 else 0
    held = set(random.Random(seed).sample(all_ids, k))

    visible, heldout = {}, {}
    for rel, src in sources.items():
        mine = set(ids[rel])
        if mine - held:
            visible[rel] = _render(src, trees[rel], rel, mine - held)
        if mine & held:
            heldout[rel] = _render(src, trees[rel], rel, mine & held)
    return visible, heldout, sorted(held)

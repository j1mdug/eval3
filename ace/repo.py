"""Working copy and pytest helpers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

JUNK_PARTS = {"__pycache__", ".hypothesis", ".pytest_cache"}
PROTECTED_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "sitecustomize.py", "usercustomize.py"}


def read(path: Path) -> str:
    """UTF-8 on every OS, with line endings left as they are."""
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def child_env(project: Path, **extra: str) -> dict[str, str]:
    """Environment for commands run in the working copy: no gateway credentials, and imports resolve to
    the copy's code rather than an editable install of the original checkout."""
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_BASE_URL", "MODEL_API_KEY")}
    paths = [str(p) for p in (project / "src", project) if p.is_dir()] + [env.get("PYTHONPATH", "")]
    return {**env, "PYTHONPATH": os.pathsep.join(filter(None, paths)), "PYTHONUTF8": "1", **extra}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


@dataclass
class WorkingCopy:
    root: Path  # top level of the copy
    project: Path  # the project inside it; differs from root when --repo is a monorepo subdirectory
    base: str

    def path(self, rel: str) -> Path:
        return self.root / rel


def create_copy(repo: Path, dest: Path) -> WorkingCopy:
    """Copy the repo's last commit into dest, tracked by a private git repo there. The user's repo
    is only read."""
    repo = repo.resolve()
    top = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    dest.mkdir(parents=True)
    with subprocess.Popen(["git", "archive", "HEAD"], cwd=top, stdout=subprocess.PIPE) as archive:
        subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, check=True)
    if archive.returncode:
        raise RuntimeError(f"git archive failed in {top}")
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "-c", "user.name=ace", "-c", "user.email=ace@localhost", "-c", "commit.gpgsign=false",
        "commit", "-qm", "base", "--no-verify")
    return WorkingCopy(dest.resolve(), dest.resolve() / repo.relative_to(top), git(dest, "rev-parse", "HEAD").strip())


def patch(wt: WorkingCopy) -> str:
    """Every change since the base, as a patch the user can `git apply` at their repo root."""
    paths = changed_files(wt)
    if paths:
        git(wt.root, "add", "-A", "--", *paths)
    return git(wt.root, "diff", "--cached", "--binary", wt.base)


def changed_files(wt: WorkingCopy) -> list[str]:
    """Paths (relative to the copy root) modified, added, or deleted since the base commit."""
    out = git(wt.root, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if not JUNK_PARTS & set(Path(path).parts):
            paths.append(path)
    return paths


def exists_at_base(wt: WorkingCopy, rel: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{wt.base}:{rel}"], cwd=wt.root, capture_output=True).returncode == 0


def restore(wt: WorkingCopy, rel: str) -> None:
    """Put a file back to its base-commit state (deleting it if it didn't exist)."""
    if exists_at_base(wt, rel):
        git(wt.root, "checkout", wt.base, "--", rel)
    else:
        wt.path(rel).unlink(missing_ok=True)


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def touches_test_config(wt: WorkingCopy, rel: str) -> bool:
    """Would this change alter how tests are collected or run?"""
    name = Path(rel).name
    if name in PROTECTED_NAMES or is_test_file(rel):
        return True
    if name == "pyproject.toml" and exists_at_base(wt, rel):
        diff = git(wt.root, "diff", wt.base, "--", rel)
        return bool(re.search(r"^[+-](?![+-]).*(pytest|hypothesis)", diff, re.M))
    return False


def find_python(repo: Path) -> str:
    for venv in (".venv", "venv"):
        for cand in (repo / venv / "bin" / "python", repo / venv / "Scripts" / "python.exe"):
            if cand.exists():
                return str(cand)
    return sys.executable


def has_module(python: str, module: str) -> bool:
    return subprocess.run([python, "-c", f"import {module}"], capture_output=True).returncode == 0


@dataclass
class TestRun:
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)  # failures and errors, including collection errors
    skipped: set[str] = field(default_factory=set)
    output: str = ""

    @property
    def syntax_error(self) -> bool:
        return bool(re.search(r"\b(SyntaxError|IndentationError)\b", self.output))

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed) + len(self.skipped)


def run_pytest(wt: WorkingCopy, python: str, paths: list[Path] | None = None, timeout: int = 1800) -> TestRun:
    env = child_env(wt.project, PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [python, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", f"--junitxml={junit}"]
        proc = subprocess.run(
            cmd + [str(p) for p in paths or []],
            cwd=wt.project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        run = TestRun(output=(proc.stdout + proc.stderr)[-6000:])
        if not junit.exists():
            run.failed.add(f"<pytest exited {proc.returncode} without results>")
            return run
        for case in ET.parse(junit).iter("testcase"):
            tid = f"{case.get('classname', '')}::{case.get('name', '')}"
            tags = {child.tag for child in case}
            if tags & {"failure", "error"}:
                run.failed.add(tid)
            elif "skipped" in tags:
                run.skipped.add(tid)
            else:
                run.passed.add(tid)
    return run

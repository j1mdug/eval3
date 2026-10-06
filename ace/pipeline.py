"""The whole pipeline: baseline -> tests -> red check -> implement -> held-out -> patch -> review."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ace import agents, heldout, prompts
from ace import repo as r


@dataclass
class Models:
    test: str
    impl: str
    review: str


@dataclass
class Result:
    status: str = "fail"
    stopped_at: str = ""
    patch: str = ""
    workdir: str = ""
    test_files: list[str] = field(default_factory=list)
    visible_tests: int = 0
    heldout_tests: list[str] = field(default_factory=list)
    heldout_failed: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    attempts: int = 0
    reverted: list[str] = field(default_factory=list)
    review: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def run(
    spec_path: Path,
    repo_path: Path,
    models: Models,
    *,
    max_retries: int = 3,
    heldout_fraction: float = 0.2,
    python: str | None = None,
    runs_dir: Path = Path.home() / ".ace" / "runs",
    log: Callable[[str], None] = print,
) -> Result:
    spec = r.read(spec_path)
    slug = re.sub(r"[^a-z0-9]+", "-", spec_path.stem.lower()).strip("-")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = (runs_dir / f"{slug}-{stamp}").resolve()
    run_dir.mkdir(parents=True)
    python = python or r.find_python(repo_path)

    wt = r.create_copy(repo_path, run_dir / "code")
    res = Result(workdir=str(wt.root))
    log(f"working copy of HEAD: {wt.root}")

    try:
        _run(spec, wt, run_dir, python, models, max_retries, heldout_fraction, res, log)
    except agents.AgentError as err:
        res.notes.append(str(err))
        log(f"agent error: {err}")

    r.write(run_dir / "result.json", json.dumps(asdict(res), indent=2))
    log(f"\n{res.status.upper()} (stopped at: {res.stopped_at})  report: {run_dir / 'result.json'}")
    if res.patch:
        log(f"changes: {res.patch}  (apply from the repo root with: git apply <path>)")
    return res


def _run(spec, wt, run_dir, python, models, max_retries, fraction, res: Result, log) -> None:  # noqa: ANN001
    res.stopped_at = "baseline"
    log("baseline: running the existing test suite")
    baseline = r.run_pytest(wt, python)
    log(f"  {len(baseline.passed)} passing, {len(baseline.failed)} already failing")

    # 1. Tests, written by model A, must fail before the feature exists.
    res.stopped_at = "tests"
    log(f"tests: writing with model {models.test}")
    hypothesis = prompts.HYPOTHESIS_YES if r.has_module(python, "hypothesis") else prompts.HYPOTHESIS_NO
    message = prompts.TEST_WRITER.format(spec=spec, hypothesis=hypothesis)
    with agents.open_agent(models.test, wt.project, log) as writer:
        for _ in range(2):
            writer.send(message)
            test_files, problem = _check_red(wt, python, res, log)
            if not problem:
                break
            log(f"  rejected: {problem.splitlines()[0]}")
            message = prompts.TEST_RETRY.format(problem=problem)
        else:
            res.notes.append(f"tests rejected: {problem}")
            return
    res.test_files = test_files
    full = {rel: r.read(wt.path(rel)) for rel in test_files}

    # 2. Hide some test functions from the implementer.
    visible, hidden, res.heldout_tests = heldout.split(full, fraction)
    for rel in test_files:
        if rel in visible:
            r.write(wt.path(rel), visible[rel])
        else:
            wt.path(rel).unlink()
    res.visible_tests = sum(src.count("def test_") for src in visible.values())
    log(f"  {res.visible_tests} visible tests, {len(res.heldout_tests)} held out")

    # 3. Implementation, by model B, against the visible tests plus the whole existing suite.
    res.stopped_at = "implementation"
    log(f"implementation: writing with model {models.impl}")
    visible_paths = [wt.path(rel) for rel in visible]
    pytest_cmd = f"{python} -m pytest " + " ".join(str(p.relative_to(wt.project)) for p in visible_paths)
    message = prompts.IMPLEMENTER.format(spec=spec, tests=", ".join(visible), pytest=pytest_cmd)
    with agents.open_agent(models.impl, wt.project, log) as impl:
        for attempt in range(1, max_retries + 2):
            res.attempts = attempt
            impl.send(message)
            reverted = _enforce(wt, visible, test_files)
            res.reverted += reverted
            output = _gate(wt, python, visible_paths, baseline, res)
            if output is None:
                log(f"  attempt {attempt}: all tests pass, no regressions")
                break
            log(f"  attempt {attempt}: failing" + (f" (reverted {', '.join(reverted)})" if reverted else ""))
            note = f"Reverted your changes to: {', '.join(reverted)}" if reverted else ""
            message = prompts.IMPL_RETRY.format(reverted=note, output=output)
        else:
            res.notes.append("implementation did not pass within the retry budget")
            _write_patch(wt, run_dir, test_files, full, res)
            return

    # 4. Held-out tests: run them alone, then restore the complete test files.
    res.stopped_at = "held-out"
    if hidden:
        for rel, src in hidden.items():
            r.write(wt.path(rel), src)
        run = r.run_pytest(wt, python, [wt.path(rel) for rel in hidden])
        res.heldout_failed = sorted(run.failed | run.skipped)
        log(f"held-out: {len(run.passed)}/{run.total} pass")
    diff = _write_patch(wt, run_dir, test_files, full, res)
    if res.heldout_failed:
        return

    # 5. Review, by model C, of the spec and the diff only.
    res.stopped_at = "review"
    log(f"review: judging with model {models.review}")
    res.review = _review(spec, diff, models.review)
    log(f"  {'pass' if res.review.get('pass') else 'fail'}: {res.review.get('summary', '')}")

    res.stopped_at = "done"
    res.status = "pass" if res.review.get("pass") and not res.review.get("cheating") else "fail"


def _check_red(wt: r.WorkingCopy, python: str, res: Result, log) -> tuple[list[str], str]:  # noqa: ANN001
    """Keep only new test files, then require every new test to fail."""
    test_files = []
    for rel in r.changed_files(wt):
        if r.is_test_file(rel) and not r.exists_at_base(wt, rel):
            test_files.append(rel)
        else:
            r.restore(wt, rel)
            res.reverted.append(rel)
            log(f"  reverted non-test change: {rel}")
    if not test_files:
        return [], "No new test files (test_*.py) were created."

    run = r.run_pytest(wt, python, [wt.path(rel) for rel in test_files])
    if run.syntax_error:
        return test_files, f"The test files do not parse:\n{run.output}"
    if run.total == 0:
        return test_files, f"pytest collected no tests:\n{run.output}"
    if run.passed:
        passing = "\n".join(sorted(run.passed))
        return test_files, f"These tests pass before the feature exists, so they test nothing new:\n{passing}"
    # A missing feature module fails the whole file at import, so this counts files, not tests.
    log(f"  red check ok: nothing passes yet ({len(run.failed)} failing results)")
    return test_files, ""


def _enforce(wt: r.WorkingCopy, visible: dict[str, str], test_files: list[str]) -> list[str]:
    """Rewrite the visible tests from memory and undo any change to test files or test config."""
    reverted = []
    for rel, src in visible.items():
        if not wt.path(rel).exists() or r.read(wt.path(rel)) != src:
            r.write(wt.path(rel), src)
            reverted.append(rel)
    for rel in r.changed_files(wt):
        if rel not in test_files and r.touches_test_config(wt, rel):
            r.restore(wt, rel)
            reverted.append(rel)
    return reverted


def _gate(wt: r.WorkingCopy, python: str, visible_paths: list[Path], baseline: r.TestRun, res: Result) -> str | None:
    """None if the visible tests all pass and nothing that passed before now fails; else pytest output."""
    new = r.run_pytest(wt, python, visible_paths)
    if new.failed or new.skipped or not new.passed:
        return new.output
    suite = r.run_pytest(wt, python)
    res.regressions = sorted(suite.failed - baseline.failed)
    return f"Previously passing tests now fail:\n{suite.output}" if res.regressions else None


def _write_patch(wt: r.WorkingCopy, run_dir: Path, test_files: list[str], full: dict[str, str], res: Result) -> str:
    """Restore the complete test files, then save tests and code as one patch for the user to apply."""
    for rel in test_files:
        r.write(wt.path(rel), full[rel])
    diff = r.patch(wt)
    r.write(run_dir / "changes.patch", diff)
    res.patch = str(run_dir / "changes.patch")
    return diff


def _review(spec: str, diff: str, model: str) -> dict:
    reply = agents.ask(model, prompts.REVIEWER.format(spec=spec, diff=diff))
    found = re.search(r"\{.*\}", reply, re.S)
    try:
        return json.loads(found.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"pass": False, "summary": f"reviewer did not reply with valid JSON: {reply[:300]}"}

"""ace login | ace config | ace init | ace run SPEC --repo PATH"""

from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path

import click

from ace.pipeline import Models, run

CONFIG = Path.home() / ".ace" / "config.json"
ENV_NAMES = {
    "base_url": "MODEL_BASE_URL", "api_key": "MODEL_API_KEY",
    "test_model": "ACE_TEST_MODEL", "impl_model": "ACE_IMPL_MODEL", "review_model": "ACE_REVIEW_MODEL",
}


def _read() -> dict[str, str]:
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def _save(**values: str | None) -> None:
    config = {**_read(), **{k: v for k, v in values.items() if v}}
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.touch(mode=0o600)
    CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")


@click.group(help="Test-first feature implementation with three models.")
def main() -> None:
    # Environment variables win over the saved config, so CI secrets override it.
    for key, value in _read().items():
        os.environ.setdefault(ENV_NAMES[key], value)


@main.command(help="Save the gateway URL and API key.")
@click.option("--base-url", prompt="Gateway URL (OpenAI-compatible)")
@click.option("--api-key", prompt="API key", hide_input=True)
def login(base_url: str, api_key: str) -> None:
    _save(base_url=base_url, api_key=api_key)
    click.echo(f"saved to {CONFIG}")


def _model_option(role: str, prompt: str):  # noqa: ANN202
    return click.option(f"--{role}-model", prompt=prompt, default=lambda: _read().get(f"{role}_model"))


@main.command(help="Choose the model for each role (Enter keeps the current one).")
@_model_option("test", "Test writer model")
@_model_option("impl", "Implementer model")
@_model_option("review", "Reviewer model")
def config(test_model: str, impl_model: str, review_model: str) -> None:
    _save(test_model=test_model, impl_model=impl_model, review_model=review_model)
    for key, value in _read().items():
        click.echo(f"{key}: {'****' + value[-4:] if key == 'api_key' else value}")


@main.command(help="Install the ace skill for Cline in the current project.")
def init() -> None:
    dest = Path(".cline/skills/ace/SKILL.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(files("ace").joinpath("SKILL.md").read_bytes())
    click.echo(f"wrote {dest} (turn on Skills in Cline: Settings > Features > Enable Skills)")


@main.command("run", help="Implement a spec in a copy of an existing repo and save the changes as a patch.")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Path to the git repo (or a project subdirectory of one).")
@click.option("--test-model", envvar="ACE_TEST_MODEL", help="Model that writes the tests.")
@click.option("--impl-model", envvar="ACE_IMPL_MODEL", help="Model that implements.")
@click.option("--review-model", envvar="ACE_REVIEW_MODEL", help="Model that reviews.")
@click.option("--max-retries", default=3, show_default=True, help="Extra implementation attempts after the first.")
@click.option("--held-out", default=0.2, show_default=True, help="Fraction of test functions hidden from the implementer.")
@click.option("--python", help="Interpreter for the repo's tests (default: its .venv, else this one).")
@click.option("--runs-dir", default=Path.home() / ".ace" / "runs", show_default=True,
              type=click.Path(file_okay=False, path_type=Path), help="Where run reports and working copies go (keep outside the repo).")
def run_cmd(
    spec: Path, repo: Path, test_model: str, impl_model: str, review_model: str,
    max_retries: int, held_out: float, python: str | None, runs_dir: Path,
) -> None:
    if not (os.environ.get("MODEL_BASE_URL") and os.environ.get("MODEL_API_KEY")):
        raise click.UsageError("no gateway set; run `ace login`")
    if not (test_model and impl_model and review_model):
        raise click.UsageError("models not set; run `ace config`")
    models = Models(test=test_model, impl=impl_model, review=review_model)
    if len({models.test, models.impl, models.review}) < 3:
        click.echo("note: the roles share a model; use different models so they don't share blind spots.\n")
    result = run(spec, repo, models, max_retries=max_retries, heldout_fraction=held_out, python=python, runs_dir=runs_dir)
    raise SystemExit(0 if result.status == "pass" else 1)

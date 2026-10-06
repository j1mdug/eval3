"""A minimal tool-using agent over any OpenAI-compatible chat completions API.

Configure with MODEL_BASE_URL (the gateway) and MODEL_API_KEY. Each agent works inside one
directory, and its file tools can't reach outside it.
"""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from ace.repo import child_env, read, write

MAX_STEPS = 80
MAX_OUTPUT = 20_000

SYSTEM = """\
You are a coding agent working in the directory {root}. Use the tools to explore and change files;
all paths are relative to that directory. Work until the task is done, then reply with a short
summary and no tool calls."""


class AgentError(RuntimeError):
    pass


def _tool(name: str, description: str, **params: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {p: {"type": "string", "description": d} for p, d in params.items()},
                "required": list(params),
            },
        },
    }


TOOLS = [
    _tool("list_files", "List files under a directory, recursively.", path="Directory, '.' for the root."),
    _tool("read_file", "Read a text file.", path="File path."),
    _tool("write_file", "Create or overwrite a file.", path="File path.", content="Full file content."),
    _tool(
        "replace_in_file", "Replace one exact occurrence of text in a file.",
        path="File path.", old="Exact text to find (must occur once).", new="Replacement text.",
    ),
    _tool("run", "Run a shell command in the working directory and return its output.", command="Command."),
]


VERBS = {"list_files": "list", "read_file": "read", "write_file": "write", "replace_in_file": "edit", "run": "run"}


def _first_line(text: str) -> str:
    return text.strip().partition("\n")[0][:160]


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n... [{len(text) - MAX_OUTPUT} chars cut]"


class Conversation:
    def __init__(self, client, model: str, root: Path, log: Callable[[str], None] = print) -> None:  # noqa: ANN001 (OpenAI client)
        self.client, self.model, self.root, self.log = client, model, root.resolve(), log
        self.tools = TOOLS
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM.format(root=self.root)}]

    def send(self, message: str) -> str:
        self.messages.append({"role": "user", "content": message})
        for _ in range(MAX_STEPS):
            try:
                reply = self.client.chat.completions.create(model=self.model, messages=self.messages, tools=self.tools)
            except Exception as err:  # noqa: BLE001 (surface any gateway error the same way)
                raise AgentError(f"model call failed ({self.model}): {err}") from err
            msg = reply.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))
            if msg.content and msg.content.strip():
                self.log(f"    {_first_line(msg.content)}")
            if not msg.tool_calls:
                return msg.content or ""
            for call in msg.tool_calls:
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": _clip(self._call(call))})
        raise AgentError(f"agent did not finish within {MAX_STEPS} steps")

    def _call(self, call) -> str:  # noqa: ANN001
        name = call.function.name
        if name not in {t["function"]["name"] for t in self.tools}:
            return f"error: no tool named {name}"
        try:
            args = json.loads(call.function.arguments or "{}")
            self.log(f"    {VERBS[name]} {_first_line(args.get('path') or args.get('command') or '')}")
            return getattr(self, f"_t_{name}")(**args)
        except Exception as err:  # noqa: BLE001 (tool errors go back to the model)
            return f"error: {err}"

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"{rel} is outside the working directory")
        return path

    def _t_list_files(self, path: str) -> str:
        skip = {".git", ".venv", "__pycache__", ".hypothesis", "node_modules"}
        files = [
            str(p.relative_to(self.root)) for p in sorted(self._path(path).rglob("*"))
            if p.is_file() and not skip & set(p.relative_to(self.root).parts)
        ]
        return "\n".join(files) or "(no files)"

    def _t_read_file(self, path: str) -> str:
        return self._path(path).read_bytes().decode("utf-8", errors="replace")

    def _t_write_file(self, path: str, content: str) -> str:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target, content)
        return f"wrote {path}"

    def _t_replace_in_file(self, path: str, old: str, new: str) -> str:
        target = self._path(path)
        text = read(target)
        if text.count(old) != 1:
            return f"error: expected exactly one match in {path}, found {text.count(old)}"
        write(target, text.replace(old, new))
        return f"edited {path}"

    def _t_run(self, command: str) -> str:
        proc = subprocess.run(
            command, shell=True, cwd=self.root, env=child_env(self.root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        return f"exit {proc.returncode}\n{proc.stdout}{proc.stderr}"


def client():  # noqa: ANN201
    from openai import OpenAI

    missing = [k for k in ("MODEL_BASE_URL", "MODEL_API_KEY") if not os.environ.get(k)]
    if missing:
        raise AgentError(f"{' and '.join(missing)} not set (run `ace login`).")
    return OpenAI(base_url=os.environ["MODEL_BASE_URL"], api_key=os.environ["MODEL_API_KEY"])


@contextmanager
def open_agent(model: str, cwd: Path, log: Callable[[str], None] = print) -> Iterator[Conversation]:
    yield Conversation(client(), model, cwd, log)


def ask(model: str, prompt: str) -> str:
    """A single model call with no tools."""
    try:
        reply = client().chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
    except Exception as err:  # noqa: BLE001 (surface any gateway error the same way)
        raise AgentError(f"model call failed ({model}): {err}") from err
    return reply.choices[0].message.content or ""

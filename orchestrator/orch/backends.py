"""How a role prompt becomes an agent run. Swappable so the orchestration
logic can be tested without model calls.

A backend is any object with: run(role, prompt, cwd) -> str
"""
import json
import subprocess


class ClaudeCLI:
    """Runs each sub-agent as a fresh, headless `claude -p` process.

    A fresh process per call is the isolation boundary between agents: no
    shared conversation, so no agent inherits another's context or drift.
    """

    TOOLS = {
        "planner": [],  # thinks only
        "executor": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
    }

    def __init__(self, model=None, timeout=900):
        self.model = model
        self.timeout = timeout

    def run(self, role, prompt, cwd):
        tools = self.TOOLS[role]
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--no-session-persistence", "--strict-mcp-config",
               "--tools", ",".join(tools)]
        if tools:
            cmd += ["--allowedTools", ",".join(tools), "--permission-mode", "acceptEdits"]
        if self.model:
            cmd += ["--model", self.model]
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"claude exited {r.returncode}: {r.stderr[-2000:]}")
        out = json.loads(r.stdout)
        if out.get("is_error"):
            raise RuntimeError(f"claude error: {out.get('result')}")
        return out.get("result", "")


class Scripted:
    """Test backend: role -> callable(prompt, cwd) -> str."""

    def __init__(self, **handlers):
        self.handlers = handlers
        self.calls = []

    def run(self, role, prompt, cwd):
        self.calls.append(role)
        return self.handlers[role](prompt, cwd)

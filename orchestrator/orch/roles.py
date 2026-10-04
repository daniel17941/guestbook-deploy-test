"""The three roles. Each does one thing and has a narrow contract.

Planner   (LLM, no tools)  requirement -> ordered steps, each with a shell
                           check and any test files that define "done".
Executor  (LLM, tools)     makes ONE step pass inside the workspace.
Verifier  (deterministic)  decides pass/fail. Not an LLM on purpose: the gate
                           should be reproducible, and the agent being judged
                           must not be able to talk its way past it.
"""
import hashlib
import json
import re

PLANNER_PROMPT = """You are the PLANNER in a planner/executor/verifier system.
Break the requirement into the smallest ordered steps that can each be verified
mechanically. Another agent will execute one step at a time; it sees only the
step goal and the workspace.

Rules:
- 1 to {max_steps} steps. Each step is one small, independently checkable change.
- "check" is a shell command run from the workspace root. Exit 0 = step done.
  It MUST fail before the step is done (it is run first to prove that).
- Put any test files the check depends on in "files" (path -> full content).
  These are written by the orchestrator and are read-only to the executor,
  so they are the contract. Prefer real tests over `test -f` style checks.
- Checks must be cumulative-safe: a later step must not break an earlier check.
- Use only tools available on a stock Linux box with python3 (stdlib) and node,
  unless the requirement says otherwise.

Reply with ONLY a JSON object, no prose:
{{"steps": [{{"id": "s1", "goal": "...", "check": "...", "files": {{"path": "content"}}}}]}}

Requirement:
{requirement}
"""

PLANNER_RETRY = "\n\nYour previous plan was rejected: {reason}\nFix it and reply with JSON only."

EXECUTOR_PROMPT = """You are the EXECUTOR in a planner/executor/verifier system.
Your working directory is an isolated workspace. Complete exactly this one step,
nothing beyond it.

Overall requirement (context only): {requirement}

Your step: {goal}

You are done when this command exits 0 from the workspace root:
    {check}

Read-only contract files (do NOT modify): {protected}

Do not run git. Do not edit files outside the workspace. When finished, reply
with one short paragraph summarising what you changed.
{feedback}"""

EXECUTOR_FEEDBACK = """
A previous attempt at this step failed verification and the workspace was
reset. Verifier output:
---
{output}
---"""


class PlanInvalid(Exception):
    pass


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise PlanInvalid("no JSON object in planner output")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise PlanInvalid(f"invalid JSON: {e}")


def validate_plan(obj, max_steps):
    steps = obj.get("steps") if isinstance(obj, dict) else None
    if not isinstance(steps, list) or not 1 <= len(steps) <= max_steps:
        raise PlanInvalid(f"'steps' must be a list of 1..{max_steps}")
    seen, clean = set(), []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            raise PlanInvalid(f"step {i} is not an object")
        sid, goal, check = st.get("id"), st.get("goal"), st.get("check")
        files = st.get("files") or {}
        if not (isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sid)):
            raise PlanInvalid(f"step {i}: bad id")
        if sid in seen:
            raise PlanInvalid(f"duplicate step id {sid}")
        if not (isinstance(goal, str) and goal.strip()):
            raise PlanInvalid(f"step {sid}: missing goal")
        if not (isinstance(check, str) and check.strip()):
            raise PlanInvalid(f"step {sid}: missing check")
        if not (isinstance(files, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in files.items())):
            raise PlanInvalid(f"step {sid}: files must map path -> string")
        for rel in files:
            if rel.startswith("/") or ".." in rel.split("/") or rel.split("/")[0] == ".git":
                raise PlanInvalid(f"step {sid}: unsafe file path {rel}")
        seen.add(sid)
        clean.append({"id": sid, "goal": goal.strip(), "check": check.strip(), "files": files})
    return clean


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Verifier:
    """Gate a step: contract files untouched, then every check so far exits 0."""

    def __init__(self, timeout=300):
        self.timeout = timeout

    def verify(self, ws, steps, protected):
        """`steps` is every step up to and including the current one, so a
        change that breaks an earlier step fails here too."""
        for rel, digest in protected.items():
            content = ws.read(rel)
            if content is None or sha(content) != digest:
                return False, f"contract file modified or deleted: {rel}"
        for step in reversed(steps):  # current step first: most useful feedback
            code, out = ws.run(step["check"], self.timeout)
            if code != 0:
                return False, f"[{step['id']}] $ {step['check']}\n(exit {code})\n{out}"
        return True, f"all {len(steps)} check(s) passed"

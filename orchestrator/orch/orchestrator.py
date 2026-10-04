"""The coordinator. It never does the work itself: it sequences roles, owns
the workspace and the log, and enforces the gates between steps.

Lifecycle of a job (every arrow is a log event):

  job_created -> plan_rejected* -> plan_accepted
    for each step:
      precheck (check must FAIL first)
      attempt_started -> attempt_verified   (repeat up to max_attempts,
                                             workspace reset before each)
      step_passed (workspace committed; commit id is the checkpoint)
  -> job_completed | job_failed
"""
import os

from .log import EventLog, next_step, replay
from .roles import (EXECUTOR_FEEDBACK, EXECUTOR_PROMPT, PLANNER_PROMPT, PLANNER_RETRY,
                    PlanInvalid, Verifier, extract_json, sha, validate_plan)
from .workspace import Workspace

MAX_PLAN_TRIES = 3


class JobLocked(Exception):
    pass


class _Lock:
    """One orchestrator per job at a time. Stale locks (dead pid) are reclaimed."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    with open(self.path) as f:
                        pid = int(f.read() or 0)
                    os.kill(pid, 0)
                    raise JobLocked(f"job is being run by pid {pid}")
                except (ProcessLookupError, ValueError):
                    os.remove(self.path)
        raise JobLocked("could not acquire lock")

    def __exit__(self, *exc):
        os.remove(self.path)


class Orchestrator:
    def __init__(self, root, backend, verifier=None, echo=print):
        self.root = root
        self.backend = backend
        self.verifier = verifier or Verifier()
        self.echo = echo

    def paths(self, job_id):
        d = os.path.join(self.root, job_id)
        return d, os.path.join(d, "log.jsonl"), os.path.join(d, "workspace")

    # -- entry points ---------------------------------------------------------

    def start(self, job_id, requirement, max_attempts=3, max_steps=8):
        d, log_path, ws_path = self.paths(job_id)
        if os.path.exists(d):
            raise FileExistsError(f"job {job_id} already exists; use resume")
        os.makedirs(d)
        checkpoint = Workspace(ws_path).init()
        EventLog(log_path).append("job_created", requirement=requirement,
                                  max_attempts=max_attempts, max_steps=max_steps,
                                  checkpoint=checkpoint)
        return self.resume(job_id)

    def resume(self, job_id):
        d, log_path, ws_path = self.paths(job_id)
        if not os.path.exists(log_path):
            raise FileNotFoundError(f"no job {job_id}")
        with _Lock(os.path.join(d, "lock")):
            log, ws = EventLog(log_path), Workspace(ws_path)
            state = replay(log.events)
            if state["status"] in ("completed", "failed"):
                return state
            # Whatever happened since the last checkpoint (crash, half-done
            # attempt) is discarded: the workspace must match the log.
            ws.reset(state["checkpoint"])
            if state["status"] == "planning":
                self._plan(log, state)
            while (state := replay(log.events))["status"] == "executing":
                step = next_step(state)
                if step is None:
                    log.append("job_completed", checkpoint=state["checkpoint"])
                    self.echo("job completed")
                else:
                    self._run_step(log, ws, state, step)
            return replay(log.events)

    def status(self, job_id):
        return replay(EventLog(self.paths(job_id)[1]).events)

    # -- roles ----------------------------------------------------------------

    def _plan(self, log, state):
        create = log.events[0]["data"]
        prompt = PLANNER_PROMPT.format(max_steps=create["max_steps"],
                                       requirement=state["requirement"])
        reason = None
        for _ in range(state["plan_rejections"], MAX_PLAN_TRIES):
            self.echo("planner: drafting plan")
            raw = self.backend.run("planner", prompt + (PLANNER_RETRY.format(reason=reason)
                                                        if reason else ""), cwd=self.root)
            try:
                steps = validate_plan(extract_json(raw), create["max_steps"])
            except PlanInvalid as e:
                reason = str(e)
                log.append("plan_rejected", reason=reason, raw=raw[:4000])
                self.echo(f"planner: rejected ({reason})")
                continue
            log.append("plan_accepted", steps=steps)
            for s in steps:
                self.echo(f"  {s['id']}: {s['goal']}")
            return
        log.append("job_failed", reason=f"no valid plan after {MAX_PLAN_TRIES} tries")

    def _run_step(self, log, ws, state, step):
        sid = step["id"]
        upto = state["plan"][: state["plan"].index(step) + 1]
        # Contract = every test file from this step and all earlier ones;
        # checks = this step's plus all earlier ones (regression gate).
        contract = {k: v for s in upto for k, v in s["files"].items()}
        protected = {k: sha(v) for k, v in contract.items()}
        prior = state["attempts"].get(sid, 0)

        if prior == 0:
            ws.reset(state["checkpoint"])
            ws.write_files(contract)
            code, out = ws.run(step["check"], self.verifier.timeout)
            log.append("precheck", step=sid, exit=code, output=out)
            if code == 0:
                log.append("job_failed", reason=f"step {sid}: check passes before any work "
                                                "was done, so it cannot verify the step")
                return

        feedback = self._last_failure(log, sid)
        for attempt in range(prior + 1, state["max_attempts"] + 1):
            self.echo(f"executor: {sid} attempt {attempt}/{state['max_attempts']}")
            ws.reset(state["checkpoint"])
            ws.write_files(contract)
            log.append("attempt_started", step=sid, attempt=attempt)
            prompt = EXECUTOR_PROMPT.format(
                requirement=state["requirement"], goal=step["goal"], check=step["check"],
                protected=", ".join(sorted(contract)) or "(none)",
                feedback=EXECUTOR_FEEDBACK.format(output=feedback) if feedback else "")
            try:
                summary = self.backend.run("executor", prompt, cwd=ws.path)
            except Exception as e:  # an agent crash is a failed attempt, not a dead job
                summary = f"executor error: {e}"
            ok, out = self.verifier.verify(ws, upto, protected)
            log.append("attempt_verified", step=sid, attempt=attempt, passed=ok,
                       summary=summary[:2000], output=out)
            self.echo(f"verifier: {sid} {'PASS' if ok else 'FAIL'}")
            if ok:
                checkpoint = ws.commit(f"{sid}: {step['goal']}")
                log.append("step_passed", step=sid, checkpoint=checkpoint)
                return
            feedback = out
        log.append("job_failed", reason=f"step {sid} failed verification "
                                        f"{state['max_attempts']} times")

    @staticmethod
    def _last_failure(log, sid):
        for ev in reversed(log.events):
            if ev["kind"] == "attempt_verified" and ev["data"]["step"] == sid:
                return ev["data"]["output"]
        return None

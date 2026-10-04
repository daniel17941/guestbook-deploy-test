"""Append-only, hash-chained event log. The log is the single source of truth.

Every decision the orchestrator makes is an event. Job state is never stored
anywhere else; it is recomputed by replaying events (see `replay`). That is
what makes resume deterministic: same log in, same state out.
"""
import hashlib
import json
import os

GENESIS = "0" * 64


class LogCorrupted(Exception):
    pass


def _digest(prev_hash, seq, kind, data):
    body = json.dumps([prev_hash, seq, kind, data], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class EventLog:
    def __init__(self, path):
        self.path = path
        self.events = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, "rb") as f:
            raw = f.read()
        lines = raw.split(b"\n")
        # A crash mid-write can leave one torn final line (no trailing newline).
        # Drop it and rewrite the file so the next append starts clean.
        if lines and lines[-1] != b"":
            torn = lines.pop()
            with open(self.path, "wb") as f:
                f.write(raw[: len(raw) - len(torn)])
                f.flush()
                os.fsync(f.fileno())
        events, prev = [], GENESIS
        for i, line in enumerate(l for l in lines if l):
            ev = json.loads(line)
            if ev["seq"] != i or ev["prev"] != prev:
                raise LogCorrupted(f"chain broken at seq {i}")
            if ev["hash"] != _digest(prev, i, ev["kind"], ev["data"]):
                raise LogCorrupted(f"hash mismatch at seq {i}")
            events.append(ev)
            prev = ev["hash"]
        return events

    def append(self, kind, **data):
        seq = len(self.events)
        prev = self.events[-1]["hash"] if self.events else GENESIS
        ev = {"seq": seq, "kind": kind, "data": data, "prev": prev,
              "hash": _digest(prev, seq, kind, data)}
        with open(self.path, "a") as f:
            f.write(json.dumps(ev, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.events.append(ev)
        return ev


def replay(events):
    """Pure function: events -> job state. No I/O, no clocks."""
    s = {"status": "new", "requirement": None, "plan": None, "plan_rejections": 0,
         "passed": [], "attempts": {}, "checkpoint": None, "error": None}
    for ev in events:
        k, d = ev["kind"], ev["data"]
        if k == "job_created":
            s.update(status="planning", requirement=d["requirement"],
                     max_attempts=d["max_attempts"], checkpoint=d["checkpoint"])
        elif k == "plan_rejected":
            s["plan_rejections"] += 1
        elif k == "plan_accepted":
            s.update(status="executing", plan=d["steps"])
        elif k == "attempt_started":
            s["attempts"][d["step"]] = d["attempt"]
        elif k == "step_passed":
            s["passed"].append(d["step"])
            s["checkpoint"] = d["checkpoint"]
        elif k == "job_completed":
            s["status"] = "completed"
        elif k == "job_failed":
            s.update(status="failed", error=d["reason"])
    return s


def next_step(state):
    for step in state["plan"] or []:
        if step["id"] not in state["passed"]:
            return step
    return None

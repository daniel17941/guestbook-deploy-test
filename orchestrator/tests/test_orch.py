import json
import os
import shutil
import subprocess
import tempfile
import unittest

from orch import JobLocked, Orchestrator, Scripted
from orch.log import EventLog, LogCorrupted
from orch.orchestrator import _Lock

TEST_ADD = "from calc import add\nassert add(2, 3) == 5\nprint('ok')\n"
TEST_MUL = "from calc import mul\nassert mul(2, 3) == 6\nprint('ok')\n"
PLAN = json.dumps({"steps": [
    {"id": "s1", "goal": "add()", "check": "python3 test_add.py", "files": {"test_add.py": TEST_ADD}},
    {"id": "s2", "goal": "mul()", "check": "python3 test_mul.py", "files": {"test_mul.py": TEST_MUL}},
]})
ADD = "def add(a, b):\n    return a + b\n"
MUL = "def mul(a, b):\n    return a * b\n"


def write(cwd, name, text, mode="w"):
    with open(os.path.join(cwd, name), mode) as f:
        f.write(text)


def good_executor(prompt, cwd):
    """Does whichever step it is asked for, correctly."""
    if "Your step: add()" in prompt:
        write(cwd, "calc.py", ADD)
    else:
        write(cwd, "calc.py", MUL, "a")
    return "done"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def orch(self, backend):
        return Orchestrator(self.root, backend, echo=lambda *_: None)

    def kinds(self, job="j"):
        return [e["kind"] for e in EventLog(os.path.join(self.root, job, "log.jsonl")).events]


class TestLog(Base):
    def test_hash_chain_detects_tampering(self):
        path = os.path.join(self.root, "log.jsonl")
        log = EventLog(path)
        log.append("a", x=1)
        log.append("b", x=2)
        with open(path) as f:
            text = f.read().replace('"x": 1', '"x": 9')
        write(self.root, "log.jsonl", text)
        with self.assertRaises(LogCorrupted):
            EventLog(path)

    def test_torn_final_line_is_dropped(self):
        path = os.path.join(self.root, "log.jsonl")
        EventLog(path).append("a", x=1)
        write(self.root, "log.jsonl", '{"seq": 1, "kin', "a")
        log = EventLog(path)
        self.assertEqual(len(log.events), 1)
        log.append("b")  # chain continues cleanly after recovery
        self.assertEqual(len(EventLog(path).events), 2)


class TestJob(Base):
    def test_one_job_end_to_end(self):
        b = Scripted(planner=lambda p, c: PLAN, executor=good_executor)
        state = self.orch(b).start("j", "calc module")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["passed"], ["s1", "s2"])
        self.assertEqual(b.calls, ["planner", "executor", "executor"])
        self.assertEqual(self.kinds(), [
            "job_created", "plan_accepted",
            "precheck", "attempt_started", "attempt_verified", "step_passed",
            "precheck", "attempt_started", "attempt_verified", "step_passed",
            "job_completed"])

    def test_invalid_plan_is_retried_with_reason(self):
        prompts = []
        def planner(p, c):
            prompts.append(p)
            return "not json" if len(prompts) == 1 else PLAN
        state = self.orch(Scripted(planner=planner, executor=good_executor)).start("j", "r")
        self.assertEqual(state["status"], "completed")
        self.assertIn("previous plan was rejected", prompts[1])

    def test_failed_attempt_is_reset_and_retried_with_feedback(self):
        prompts = []
        def executor(p, cwd):
            prompts.append(p)
            if len(prompts) == 1:
                write(cwd, "calc.py", "def add(a, b):\n    return a - b\n")
                write(cwd, "junk.txt", "leftover")
                return "buggy"
            assert not os.path.exists(os.path.join(cwd, "junk.txt")), "not reset"
            return good_executor(p, cwd)
        state = self.orch(Scripted(planner=lambda p, c: PLAN, executor=executor)).start("j", "r")
        self.assertEqual(state["status"], "completed")
        self.assertIn("AssertionError", prompts[1])  # verifier output fed back

    def test_gives_up_after_max_attempts(self):
        b = Scripted(planner=lambda p, c: PLAN, executor=lambda p, c: "did nothing")
        state = self.orch(b).start("j", "r", max_attempts=2)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(b.calls.count("executor"), 2)

    def test_editing_contract_files_fails_verification(self):
        def cheat(p, cwd):
            write(cwd, "test_add.py", "print('ok')\n")
            return "made the test pass"
        state = self.orch(Scripted(planner=lambda p, c: PLAN, executor=cheat)).start(
            "j", "r", max_attempts=1)
        self.assertEqual(state["status"], "failed")
        ev = EventLog(os.path.join(self.root, "j", "log.jsonl")).events
        self.assertIn("contract file modified", [e for e in ev if e["kind"] == "attempt_verified"][0]["data"]["output"])

    def test_regression_in_earlier_step_fails(self):
        def executor(p, cwd):
            if "Your step: add()" in p:
                write(cwd, "calc.py", ADD)
            else:
                write(cwd, "calc.py", MUL)  # overwrites add() -> breaks s1
            return "x"
        state = self.orch(Scripted(planner=lambda p, c: PLAN, executor=executor)).start(
            "j", "r", max_attempts=1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["passed"], ["s1"])

    def test_check_that_already_passes_is_rejected(self):
        plan = json.dumps({"steps": [{"id": "s1", "goal": "g", "check": "true"}]})
        b = Scripted(planner=lambda p, c: plan, executor=good_executor)
        state = self.orch(b).start("j", "r")
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("executor", b.calls)

    def test_resume_after_crash_skips_finished_steps(self):
        def crashing(p, cwd):
            if "Your step: mul()" in p:
                write(cwd, "half_done.txt", "partial")
                raise KeyboardInterrupt  # simulates the process dying mid-step
            return good_executor(p, cwd)
        with self.assertRaises(KeyboardInterrupt):
            self.orch(Scripted(planner=lambda p, c: PLAN, executor=crashing)).start("j", "r")
        # A SIGKILL'd runner leaves its lock behind; simulate one with a dead pid.
        dead = subprocess.Popen(["true"]); dead.wait()
        write(os.path.join(self.root, "j"), "lock", str(dead.pid))

        b = Scripted(planner=lambda p, c: PLAN, executor=good_executor)
        state = self.orch(b).resume("j")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(b.calls, ["executor"])  # no re-plan, s1 not redone
        ws = os.path.join(self.root, "j", "workspace")
        self.assertFalse(os.path.exists(os.path.join(ws, "half_done.txt")))

    def test_resume_is_idempotent_on_finished_job(self):
        b = Scripted(planner=lambda p, c: PLAN, executor=good_executor)
        self.orch(b).start("j", "r")
        n = len(self.kinds())
        self.orch(b).resume("j")
        self.assertEqual(len(self.kinds()), n)

    def test_jobs_are_isolated(self):
        b = Scripted(planner=lambda p, c: PLAN, executor=good_executor)
        o = self.orch(b)
        o.start("a", "r")
        o.start("b", "r")
        wa, wb = (os.path.join(self.root, j, "workspace") for j in "ab")
        self.assertNotEqual(os.path.realpath(wa), os.path.realpath(wb))
        self.assertTrue(os.path.isdir(os.path.join(wa, ".git")))

    def test_live_lock_blocks_second_runner(self):
        os.makedirs(os.path.join(self.root, "x"))
        lock = os.path.join(self.root, "x", "lock")
        with _Lock(lock):
            with self.assertRaises(JobLocked):
                _Lock(lock).__enter__()


if __name__ == "__main__":
    unittest.main()

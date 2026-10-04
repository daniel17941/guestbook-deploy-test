"""Usage:
  python -m orch run JOB.json [--root runs] [--model M]
  python -m orch resume JOB_ID [--root runs]
  python -m orch status JOB_ID [--root runs]

JOB.json: {"id": "my-job", "requirement": "...", "max_attempts": 3, "max_steps": 8}
"""
import argparse
import json
import sys

from .backends import ClaudeCLI
from .orchestrator import Orchestrator


def main(argv=None):
    p = argparse.ArgumentParser(prog="orch")
    p.add_argument("--root", default="runs")
    p.add_argument("--model")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("job_file")
    sub.add_parser("resume").add_argument("job_id")
    sub.add_parser("status").add_argument("job_id")
    a = p.parse_args(argv)

    o = Orchestrator(a.root, ClaudeCLI(model=a.model))
    if a.cmd == "run":
        job = json.load(open(a.job_file))
        state = o.start(job["id"], job["requirement"], job.get("max_attempts", 3),
                        job.get("max_steps", 8))
    elif a.cmd == "resume":
        state = o.resume(a.job_id)
    else:
        state = o.status(a.job_id)
    plan = state["plan"] or []
    print(json.dumps({"status": state["status"], "error": state["error"],
                      "steps": [{"id": s["id"], "passed": s["id"] in state["passed"]}
                                for s in plan]}, indent=2))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())

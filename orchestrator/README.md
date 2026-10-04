# orch: a minimal planner/executor/verifier orchestrator

This is the smallest version that takes **one job from start to finish**. A
coordinator hands the work to sub-agents with narrow roles. Each job runs in
its own workspace, and an append-only log records every decision so the job
can be resumed from where it stopped.

It uses only the Python stdlib. The live agents are headless `claude -p`
processes.

```
python3 -m orch run jobs/example.json     # plan → execute → verify, step by step
python3 -m orch status roman-numerals     # state, replayed from the log
python3 -m orch resume roman-numerals     # continue after a crash or Ctrl-C
python3 -m unittest                       # 13 tests, no model calls
```

## Roles

| Role | Is | Does | Cannot |
|---|---|---|---|
| Orchestrator | code | sequences roles, owns the log and workspace, enforces gates | do any of the work |
| Planner | LLM, no tools | requirement → 1–N small steps, each with a shell `check` and contract test files | touch the workspace |
| Executor | LLM, file + shell tools | makes **one** step's check pass | edit contract files (hash-checked), see other steps' prompts |
| Verifier | code | pass/fail | be persuaded |

The verifier is not an LLM. The gate needs to give the same answer every
time, and the agent being judged must not be able to argue its way past it.

## Gates (why "verified" means something)

1. **Plan schema**: malformed plans are rejected and the planner is retried with the reason (max 3).
2. **Red before green**: each step's check must fail *before* the executor starts. A check that already passes can't prove anything, so the job stops.
3. **Contract integrity**: the planner's test files are written by the orchestrator and their hashes are checked after each attempt. If the executor edits a test to make it pass, that attempt fails.
4. **Regression**: verifying step N re-runs the checks for steps 1…N.
5. **Bounded retries**: before each attempt the workspace is reset to the last checkpoint, and the verifier's output is passed to the next attempt. After `max_attempts` failures the job fails.

## Isolation and the log

- `runs/<job>/workspace/` is a separate directory with its own git repo. Git is used only for checkpoints: each step that passes is committed, and every attempt or resume first hard-resets to the last checkpoint recorded in the log. That keeps the workspace in step with the log.
- `runs/<job>/log.jsonl` is append-only, written with fsync and hash-chained, so tampering or corruption is detected on load. A torn last line from a crash is dropped. The log is the **only** state: `replay(events)` is a pure function, so resume gives the same result every time.
- `runs/<job>/lock` allows only one runner per job. A lock left by a dead process is reclaimed.

## Known limits (deliberate for v0)

- **Checks are only as good as the plan.** The verifier proves the checks pass, not that they fully capture the requirement. Next step: a plan-review gate (an LLM critic or a human approval) before execution.
- **Isolation is per directory, not a sandbox.** The executor's Bash can still reach outside its workspace. For untrusted work, run each job in a container.
- The executor can read the contract tests and overfit to them. The hash check stops it editing them; it doesn't stop overfitting.
- Steps run one at a time. A crash during an attempt uses up that attempt. There is no cost or time budget yet.

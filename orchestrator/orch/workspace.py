"""Per-job isolated workspace: its own directory and its own git repo.

Git is used only as a checkpoint mechanism. After each verified step the
orchestrator commits; before every attempt (and on resume) it hard-resets to
the last checkpoint recorded in the log, so the workspace always matches the
log and a failed attempt can never leak into the next one.
"""
import os
import subprocess

GIT = ["git", "-c", "user.name=orchestrator", "-c", "user.email=orch@localhost",
       "-c", "commit.gpgsign=false"]


class Workspace:
    def __init__(self, path):
        self.path = path

    def _git(self, *args):
        r = subprocess.run(GIT + list(args), cwd=self.path, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def init(self):
        os.makedirs(self.path, exist_ok=True)
        self._git("init", "-q", "-b", "main")
        return self.commit("workspace created", allow_empty=True)

    def commit(self, message, allow_empty=False):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message, *(["--allow-empty"] if allow_empty else []))
        return self._git("rev-parse", "HEAD")

    def reset(self, checkpoint):
        self._git("reset", "-q", "--hard", checkpoint)
        self._git("clean", "-q", "-fdx")

    def write_files(self, files):
        for rel, content in files.items():
            full = self._safe(rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(content)

    def read(self, rel):
        try:
            with open(self._safe(rel)) as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _safe(self, rel):
        full = os.path.realpath(os.path.join(self.path, rel))
        if not full.startswith(os.path.realpath(self.path) + os.sep):
            raise ValueError(f"path escapes workspace: {rel}")
        return full

    def run(self, command, timeout):
        try:
            r = subprocess.run(command, shell=True, cwd=self.path, capture_output=True,
                               text=True, timeout=timeout)
            return r.returncode, (r.stdout + r.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"

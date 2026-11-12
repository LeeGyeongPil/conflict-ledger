"""Shared helpers: real throwaway git repositories with a conflict in progress."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import conflict_ledger as cl

BASE = [f"line {i}" for i in range(1, 13)]


def scenario():
    """The probe scenario: base 12 lines; ours/theirs change line 2 differently, line 7 by whitespace only,
    ours deletes line 5, theirs adds a line before line 11."""
    theirs = list(BASE)
    theirs[1] = "line 2 THEIRS"
    theirs[6] = "line 7   "
    theirs.insert(10, "added by theirs")
    ours = list(BASE)
    ours[1] = "line 2 OURS"
    ours[6] = "line 7 "
    del ours[4]
    good = list(ours)
    good[1] = "line 2 OURS + THEIRS"
    good.insert(good.index("line 11"), "added by theirs")
    return ours, theirs, good


@pytest.fixture(autouse=True)
def pinned_environment(monkeypatch, tmp_path):
    """Fix the language and keep the user's git configuration out of the tests."""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONFLICT_LEDGER_LANG", "en")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "tester")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "tester@example.invalid")
    monkeypatch.chdir(tmp_path)


class Sandbox:
    """A repository plus helpers to write files, run git and run the command line in-process."""

    def __init__(self, root: Path, capsys) -> None:
        self.root = root
        self.capsys = capsys

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False)
        if check and r.returncode != 0:
            raise AssertionError(f"git {args} failed: {r.stderr}")
        return r

    def write(self, name: str, content: str | bytes | list[str]) -> Path:
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, list):
            content = "".join(x + "\n" for x in content)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run(self, *args: str) -> tuple[int, str, str]:
        code = cl.main(["--repo", str(self.root), *args])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err

    def stages(self) -> str:
        return self.git("ls-files", "-u").stdout


def make_repo(root: Path, capsys, name: str = "f.txt", base=None, ours=None, theirs=None, mode: str = "merge") -> Sandbox:
    """Create main (ours) and feature (theirs) from a base commit and start a merge or a rebase that conflicts."""
    sb = Sandbox(root, capsys)
    root.mkdir(parents=True, exist_ok=True)
    sb.git("init", "-q", "-b", "main")
    sb.write(name, BASE if base is None else base)
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write(name, theirs)
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write(name, ours)
    sb.commit("ours")
    if mode == "merge":
        sb.git("merge", "--no-edit", "feature", check=False)
    elif mode == "rebase":
        sb.git("checkout", "-q", "feature")
        sb.git("rebase", "main", check=False)
    elif mode == "cherry-pick":
        sb.git("cherry-pick", "feature", check=False)
    return sb


@pytest.fixture
def conflict(tmp_path, capsys):
    ours, theirs, good = scenario()
    sb = make_repo(tmp_path / "repo", capsys, ours=ours, theirs=theirs)
    sb.ours, sb.theirs, sb.good = ours, theirs, good
    return sb


@pytest.fixture
def run_cli(capsys):
    """Run the command line in-process: returns (exit code, stdout, stderr)."""
    def run(*args: str) -> tuple[int, str, str]:
        code = cl.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err
    return run

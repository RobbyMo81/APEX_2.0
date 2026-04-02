from __future__ import annotations

# Co-authored by FORGE (Session: forge-20260328235846-3946349)
# V2-037: Python Git Branch and Commit Ownership
# Source of truth: forge.sh lines 637-650 (ensure_branch) and 794-810 (git section of mark_story_passing)

import subprocess
from pathlib import Path


def _git_run(repo_root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a git command in repo_root and return the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=check,
    )


def _is_git_repo(repo_root: Path) -> bool:
    """Return True if repo_root is inside a properly initialized git repository."""
    result = _git_run(repo_root, "rev-parse", "--git-dir")
    return result.returncode == 0


def ensure_branch(repo_root: Path, branch_name: str) -> None:
    """Mirror forge.sh ensure_branch().

    Branch selection rule: the caller resolves branch_name from PRD .branchName,
    defaulting to 'forge/feature' when absent.

    Branch ensure behavior:
    - Create the branch if refs/heads/<branch_name> does not exist.
    - Switch to it when the current branch differs.
    - No-op if already on the correct branch.
    - No-op if repo_root is not a properly initialized git repository.
    """
    if not _is_git_repo(repo_root):
        return

    ref_check = _git_run(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    if ref_check.returncode != 0:
        # Branch does not exist — create it
        _git_run(repo_root, "checkout", "-b", branch_name, check=True)
    else:
        # Branch exists — switch only if current branch differs
        current = _git_run(repo_root, "branch", "--show-current")
        if current.stdout.strip() != branch_name:
            _git_run(repo_root, "checkout", branch_name, check=True)


def commit_story_pass(
    repo_root: Path,
    story_id: str,
    story_title: str,
    session_id: str,
    iteration: int,
) -> None:
    """Mirror the git section of forge.sh mark_story_passing().

    Runs git add -A then commits with the exact Forge story-pass format:
      Subject: forge(<story_id>): <story_title>
      Body:
        Session: <session_id>
        Iteration: <iteration>

    No failure when there is nothing new to commit — mirrors the Bash
    `|| warn "Nothing new to commit for this story."` behavior.
    """
    if not _is_git_repo(repo_root):
        return

    _git_run(repo_root, "add", "-A", check=True)
    commit_msg = (
        f"forge({story_id}): {story_title}\n\n"
        f"Session: {session_id}\n"
        f"Iteration: {iteration}"
    )
    _git_run(
        repo_root,
        "commit",
        "--no-verify",
        "-m",
        commit_msg,
        # No check=True — silently absorbs "nothing to commit" exits
    )

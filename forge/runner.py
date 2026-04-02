from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from .agents import AgentTask, get_backend
from .archive import ArchiveResult, archive_if_needed
from .gates import generate_gates
from .git import commit_story_pass, ensure_branch
from .governance import check_backlog, check_close, check_story
from .memory import ForgeMemory, init_memory, register_abnormal_exit_handler
from .models import ForgeConfig
from .prd import UserStory, load_prd
from .preflight import run_preflight
from .process import CommandResult, run_command
from .sidecars import SidecarOrchestrator, load_sidecars
from .workspace import ensure_hidden_workspace

# The COMPLETE marker is printed to stdout to signal mission completion to the
# outer shell or CI harness.  Matches the Bash contract: echo "<promise>COMPLETE</promise>"
COMPLETE_MARKER = "<promise>COMPLETE</promise>"


@dataclass(frozen=True, slots=True)
class RunOnceResult:
    story_id: str | None
    status: str
    gates_result: int | None
    backend_result: int | None
    archive_result: ArchiveResult


@dataclass(frozen=True, slots=True)
class RunMainResult:
    status: str           # "complete", "paused", "lint_failed"
    iterations_used: int
    remaining_story_ids: list[str]


class StoryExecutionError(RuntimeError):
    """Raised by run_agent_command() on backend failure or timeout.

    Carries timed_out and returncode so run_once() can route the failure
    into the correct audit event (BACKEND_TIMEOUT vs BACKEND_ERROR) without
    re-parsing the message string.
    """

    def __init__(self, message: str, *, timed_out: bool = False, returncode: int = 1) -> None:
        super().__init__(message)
        self.timed_out = timed_out
        self.returncode = returncode


def _output_snippet(text: str, limit: int = 800) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "<empty>"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n...[truncated]..."


@dataclass(frozen=True, slots=True)
class _EnvBootstrapState:
    session_id: str
    previous_session_id: str | None
    previous_max_iterations: str | None


def _bootstrap_run_env(max_iterations: int) -> _EnvBootstrapState:
    """Ensure the Python-owned run path has the same core env context as Bash.

    The Bash wrapper always exports FORGE_SESSION_ID / FORGE_MAX_ITERATIONS /
    FORGE_ITERATION before calling into Python. Direct `python -m forge run`
    must synthesize the same state or the memory lifecycle will be partial and
    DB rows can remain inconsistent after failed iterations.
    """
    previous_session_id = os.environ.get("FORGE_SESSION_ID")
    previous_max_iterations = os.environ.get("FORGE_MAX_ITERATIONS")
    session_id = previous_session_id
    if not session_id:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        session_id = f"forge-python-{stamp}-{os.getpid()}"
        os.environ["FORGE_SESSION_ID"] = session_id
    os.environ["FORGE_MAX_ITERATIONS"] = str(max_iterations)
    return _EnvBootstrapState(
        session_id=session_id,
        previous_session_id=previous_session_id,
        previous_max_iterations=previous_max_iterations,
    )


def _restore_run_env(state: _EnvBootstrapState, previous_iteration: str | None) -> None:
    if state.previous_session_id is None:
        os.environ.pop("FORGE_SESSION_ID", None)
    else:
        os.environ["FORGE_SESSION_ID"] = state.previous_session_id

    if state.previous_max_iterations is None:
        os.environ.pop("FORGE_MAX_ITERATIONS", None)
    else:
        os.environ["FORGE_MAX_ITERATIONS"] = state.previous_max_iterations

    if previous_iteration is None:
        os.environ.pop("FORGE_ITERATION", None)
    else:
        os.environ["FORGE_ITERATION"] = previous_iteration


def check_all_complete(prd_file: Path) -> bool:
    """Return True if every userStory in prd.json has passes=true."""
    data = json.loads(prd_file.read_text(encoding="utf-8"))
    stories = data.get("userStories", [])
    return bool(stories) and all(s.get("passes", False) for s in stories)


def run_final_lint(repo_root: Path) -> CommandResult:
    """Run npm run lint --silent and return the CommandResult."""
    return run_command(
        ["npm", "run", "lint", "--silent"],
        cwd=repo_root,
        check=False,
    )


def _resolve_memory_client_path(repo_root: Path) -> Path:
    """Resolve the forge-memory-client.ts path for SIC/staging commands.

    Live target repos keep the runtime copy in `.forge/forge-memory-client.ts`.
    The repo root copy may not exist in application test targets.
    """
    workspace_dir = Path(os.environ.get("FORGE_WORKSPACE_DIR", ".forge"))
    workspace_path = workspace_dir if workspace_dir.is_absolute() else repo_root / workspace_dir
    workspace_client = workspace_path / "forge-memory-client.ts"
    if workspace_client.exists():
        return workspace_client
    return repo_root / "forge-memory-client.ts"


def trigger_github_cycle(
    mem: ForgeMemory,
    session_id: str,
    session_status: str,
) -> None:
    """Dispatch the GitHub failure-cycle notification.

    Mirrors forge.sh trigger_github_cycle() (lines 528-542).

    V2-039 clarification: *session_status* is the actual triggering session
    status (failed or paused), preserved in the audit detail rather than
    collapsed to a constant.

    Skip silently with a warning when the gh CLI is not authenticated.
    """
    print("[GITHUB CYCLE] Terminal failure detected. Phoning home...", flush=True)

    try:
        subprocess.run(
            ["gh", "auth", "status"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[GITHUB CYCLE] Skipping: gh CLI not authenticated.", flush=True)
        return

    # Mirrors: memory_audit "$SESSION_ID" "" "github-notifier"
    #          "TRIGGER_GITHUB_CYCLE" "terminal_failure" "status=<status>"
    mem.audit(
        session_id,
        None,
        "github-notifier",
        "TRIGGER_GITHUB_CYCLE",
        "terminal_failure",
        f"status={session_status}",
    )
    print("[GITHUB CYCLE] Notification dispatched to sidecar.", flush=True)


def trigger_sic(
    mem: ForgeMemory | None,
    session_id: str | None,
    repo_root: Path | None = None,
) -> None:
    """SIC trigger dispatch.  Mirrors forge.sh trigger_sic() (lines 544-576).

    Acceptance criteria (V2-039):
    1. Skip when agent_iterations count for SESSION_ID is 0.
    2. Invoke forge-memory-client.ts stage <SESSION_ID>, read shs.score and
       shs.mode from the manifest, emit SIC_TRIGGER audit.
    3. Trigger GitHub cycle when mode is AUDIT or session status is
       failed/paused.
    """
    if mem is None or session_id is None:
        return

    # 1. Telemetry gate: skip if 0 iterations
    iter_count: int = (
        mem._scalar(
            "SELECT count(*) FROM agent_iterations WHERE session_id=?;",
            (session_id,),
        )
        or 0
    )
    if iter_count == 0:
        print(
            f"SIC Skipped: INSUFFICIENT_TELEMETRY (0 iterations found for session {session_id})",
            flush=True,
        )
        return

    # 2. Run ForgeRefiner & Stage
    cwd = repo_root or Path.cwd()
    client_path = _resolve_memory_client_path(cwd)
    print(f"Running ForgeRefiner for session {session_id}...", flush=True)
    try:
        subprocess.run(
            [
                "npx",
                "ts-node",
                "--project",
                "tsconfig.json",
                str(client_path),
                "stage",
                session_id,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"SIC stage step failed: {exc}", flush=True)
        return

    manifest_path = cwd / f"staged-rules/session_{session_id}/manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        score = manifest["shs"]["score"]
        mode = manifest["shs"]["mode"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"SIC manifest read failed: {exc}", flush=True)
        return

    print(f"SIC Analysis Complete. SHS: {score} | Mode: {mode}", flush=True)
    mem.audit(
        session_id,
        None,
        None,
        "SIC_TRIGGER",
        "ForgeRefiner",
        f"score={score} mode={mode}",
    )

    # 3. GitHub Cycle: trigger on AUDIT mode or terminal failure status
    session_status: str = (
        mem._scalar(
            "SELECT status FROM forge_sessions WHERE id=?;",
            (session_id,),
        )
        or "unknown"
    )
    if mode == "AUDIT" or session_status in ("failed", "paused"):
        trigger_github_cycle(mem, session_id, session_status)


def append_progress(
    progress_file: Path,
    story: UserStory,
    status: str,
    session_id: str | None = None,
    iteration: int | None = None,
) -> None:
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).date().isoformat()
    session = session_id or "forge-python"
    iter_value = iteration if iteration is not None else 1
    normalized = status.upper()
    with progress_file.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n[{stamp}] Story [{story.id}]: {story.title}\n"
            f"STATUS: {normalized} | Session: {session} | Iteration: {iter_value}\n"
        )
        # Mirror Bash mark_story_passing(): append --- separator only for PASS records.
        if normalized == "PASS":
            handle.write("---\n")


def build_agent_payload(
    config: ForgeConfig,
    story: UserStory,
    session_id: str | None = None,
    iteration: int | None = None,
) -> str:
    progress_file = config.workspace_dir / "progress.txt"
    progress_text = (
        progress_file.read_text(encoding="utf-8") if progress_file.exists() else "(no prior progress)"
    )

    prompt_file = config.workspace_dir / "prompt.md"
    prompt_text = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else None

    startup_report_file = config.workspace_dir / "forge-startup-report.md"
    startup_report_text = (
        startup_report_file.read_text(encoding="utf-8")
        if startup_report_file.exists()
        else "(startup report not found — check forge-memory.sh ran cleanly)"
    )

    prd_text = (
        config.prd_file.read_text(encoding="utf-8")
        if config.prd_file.exists()
        else "(prd not found)"
    )

    sections: list[str] = []

    if prompt_text is not None:
        sections.append(prompt_text)

    if session_id is not None or iteration is not None:
        db_path = config.workspace_dir / "forge-memory.db"
        startup_report_path = config.workspace_dir / "forge-startup-report.md"
        sections.append(
            "\n".join([
                "---",
                "## FORGE SESSION CONTEXT",
                "",
                f"**Session ID:** {session_id or '(unknown)'}",
                f"**Iteration:** {iteration if iteration is not None else '(unknown)'}",
                f"**Memory DB:** {db_path}",
                f"**Workspace Dir:** {config.workspace_dir}",
                "",
                f"Your primary briefing document is {startup_report_path} — read it first (Function 0 requires this).",
                f"Use ForgeMemory ({config.workspace_dir}/forge-memory-client.ts) for all entry/exit obligations.",
            ])
        )

    sections.extend([
        "---",
        f"FORGE STORY ID: {story.id}",
        f"FORGE STORY TITLE: {story.title}",
        f"FORGE PRD FILE: {config.prd_file}",
        f"FORGE WORKSPACE DIR: {config.workspace_dir}",
        f"FORGE PROGRESS FILE: {progress_file}",
        "--- CURRENT STORY ---",
        json.dumps(story.raw, indent=2, sort_keys=True),
        "--- PRD STATE ---",
        prd_text,
        "--- PROGRESS LOG ---",
        progress_text,
        "--- MEMORY STARTUP REPORT ---",
        startup_report_text,
    ])

    return "\n".join(sections)


def run_agent_command(
    config: ForgeConfig,
    story: UserStory,
    command_template_override: str | None,
    env: Mapping[str, str],
    session_id: str | None = None,
    iteration: int | None = None,
) -> CommandResult:
    task = AgentTask(
        story_id=story.id,
        story_title=story.title,
        payload=build_agent_payload(config, story, session_id=session_id, iteration=iteration),
        cwd=config.repo_root,
        env=env,
        prd_file=config.prd_file,
        workspace_dir=config.workspace_dir,
        progress_file=config.workspace_dir / "progress.txt",
        startup_report_file=config.workspace_dir / "forge-startup-report.md",
        story_data=story.raw,
        timeout=config.agent_timeout_seconds,
        command_template_override=command_template_override,
        session_id=session_id,
        iteration=iteration,
    )
    backend = get_backend(config.agent_backend)
    result = backend.invoke(task)
    if result.returncode != 0:
        reason = "timed out" if result.timed_out else f"failed with exit {result.returncode}"
        raise StoryExecutionError(
            f"{backend.name} backend {reason}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
            timed_out=result.timed_out,
            returncode=result.returncode,
        )
    return CommandResult(
        args=result.command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
    )


def run_quality_gates(
    repo_root: Path,
    gates_file: Path,
    env: Mapping[str, str],
) -> CommandResult:
    return run_command(["bash", str(gates_file)], cwd=repo_root, env=env, check=False)


def run_once(
    config: ForgeConfig,
    source_root: Path,
    agent_command: str | None = None,
) -> RunOnceResult:
    ensure_hidden_workspace(
        repo_root=config.repo_root,
        workspace_dir=config.workspace_dir,
        source_root=source_root,
    )
    run_preflight(config)

    # ── Memory lifecycle init ──────────────────────────────────────────
    session_id = os.environ.get("FORGE_SESSION_ID")
    iteration_str = os.environ.get("FORGE_ITERATION")
    iteration = int(iteration_str) if iteration_str and iteration_str.isdigit() else 1
    max_iterations = int(os.environ.get("FORGE_MAX_ITERATIONS", "3"))
    startup_report_file = config.workspace_dir / "forge-startup-report.md"
    db_path = config.workspace_dir / "forge-memory.db"

    mem: ForgeMemory | None = None
    _closed_flag: dict[str, bool] | None = None

    if session_id is not None:
        prd_for_init = load_prd(config.prd_file)
        mem = init_memory(
            db_path=db_path,
            session_id=session_id,
            branch_name=prd_for_init.branch_name,
            project_name=prd_for_init.project_name,
            max_iterations=max_iterations,
            forge_db_str=str(db_path),
            prd_file=config.prd_file,
            report_file=startup_report_file,
        )
        _closed_flag = register_abnormal_exit_handler(mem, session_id)

    prd = load_prd(config.prd_file)
    archive_result = archive_if_needed(config.repo_root, config.workspace_dir, prd)

    # ── Branch ownership (V2-037) ──────────────────────────────────────────
    # Mirrors forge.sh step 5: ensure_branch runs after archive and before story
    # selection so the commit lands on the correct branch every iteration.
    ensure_branch(config.repo_root, prd.branch_name)

    story = prd.next_story()
    if story is None:
        # UAP completion gate: run final lint before declaring the mission complete.
        # Mirrors forge.sh uap_gate() — block on lint failure, emit COMPLETE on success.
        lint_result = run_final_lint(config.repo_root)
        if lint_result.returncode != 0:
            if mem is not None and session_id is not None:
                mem.post_message(
                    session_id, 0, None, "BLOCKER",
                    "Final Linting Failed",
                    "All stories passed but 'npm run lint' failed. Fix lint errors to close the session.",
                )
            if _closed_flag is not None:
                _closed_flag["done"] = True
            return RunOnceResult(
                story_id=None,
                status="lint_failed",
                gates_result=lint_result.returncode,
                backend_result=None,
                archive_result=archive_result,
            )
        if mem is not None and session_id is not None:
            mem.close_session(session_id, "complete")
            mem.post_message(
                session_id, 0, None, "STATUS",
                "All stories complete",
                "All PRD stories passed and linting verified. Session closed cleanly.",
            )
            trigger_sic(mem, session_id, config.repo_root)
            if _closed_flag is not None:
                _closed_flag["done"] = True
        print(COMPLETE_MARKER)
        return RunOnceResult(
            story_id=None,
            status="complete",
            gates_result=lint_result.returncode,
            backend_result=None,
            archive_result=archive_result,
        )

    state_file = config.workspace_dir / "governance_state.json"
    backlog_errors = check_backlog(config.prd_file, config.repo_root / "docs" / "AGENTIC_WORKLOG_LOOP.md", state_file)
    if backlog_errors:
        raise StoryExecutionError(f"Governance backlog gate failed:\n" + "\n".join(backlog_errors))

    story_errors = check_story(story.id, config.prd_file)
    if story_errors:
        raise StoryExecutionError(f"Governance entry gate failed:\n" + "\n".join(story_errors))

    if mem is not None and session_id is not None:
        mem.start_iteration(session_id, iteration, story.id, story.title)

    # ── Sidecar orchestration (V2-035) ────────────────────────────────────
    sidecars = load_sidecars(config.prd_file)
    orch = SidecarOrchestrator(db_path=db_path, repo_root=config.repo_root)
    if sidecars:
        orch.start_all(sidecars)

    progress_file = config.workspace_dir / "progress.txt"

    agent_env: dict[str, str] = {
        "FORGE_PRD_FILE": str(config.prd_file),
        "FORGE_WORKSPACE_DIR": str(config.workspace_dir),
        "FORGE_STORY_ID": story.id,
        "FORGE_AGENT_BACKEND": config.agent_backend,
        "FORGE_STARTUP_REPORT_FILE": str(startup_report_file),
    }
    if session_id is not None:
        agent_env["FORGE_SESSION_ID"] = session_id
    if iteration is not None:
        agent_env["FORGE_ITERATION"] = str(iteration)

    try:
        try:
            run_agent_command(config, story, agent_command, agent_env, session_id=session_id, iteration=iteration)
        except StoryExecutionError as exc:
            # ── Backend failure: BACKEND_TIMEOUT or BACKEND_ERROR ────────────────
            # Mirrors forge.sh run_iteration() lines 726-741 and mark_story_failed()
            # lines 813-821.  Story remains passes=False — eligible for retry.
            if mem is not None and session_id is not None:
                timeout_secs = config.agent_timeout_seconds
                if exc.timed_out:
                    mem.audit(
                        session_id, iteration, story.id,
                        "BACKEND_TIMEOUT", "python-forge",
                        f"timeout={timeout_secs}s",
                    )
                else:
                    mem.audit(
                        session_id, iteration, story.id,
                        "BACKEND_ERROR", "python-forge",
                        f"exit_code={exc.returncode}",
                    )
                # mark_story_failed: end_iteration(fail, fail) + STORY_FAIL audit
                mem.end_iteration(session_id, iteration, story.id, "fail", "fail")
                mem.audit(session_id, iteration, story.id, "STORY_FAIL", "prd.json", "")
                mem.post_message(
                    session_id, iteration, story.id, "WARNING",
                    f"[{story.id}] Backend failed iter {iteration}",
                    "Backend execution failed. Story NOT passing. Next iteration will retry.",
                )
            append_progress(progress_file, story, "fail", session_id=session_id, iteration=iteration)
            return RunOnceResult(
                story_id=story.id,
                status="timeout" if exc.timed_out else "fail",
                gates_result=None,
                backend_result=exc.returncode,
                archive_result=archive_result,
            )

        gates_file, _ = generate_gates(
            repo_root=config.repo_root,
            workspace_dir=config.workspace_dir,
            source_root=source_root,
        )
        gate_result = run_quality_gates(config.repo_root, gates_file, agent_env)
    finally:
        if sidecars:
            orch.reap_all()

    if gate_result.returncode == 0:
        prd.mark_story(story.id, True)
        prd.save()
        append_progress(progress_file, story, "pass", session_id=session_id, iteration=iteration)

        # ── Story-pass git commit (V2-037) ────────────────────────────────────
        # Mirrors forge.sh mark_story_passing() git section: git add -A + commit
        # with the frozen subject/body format.  No-op when nothing to commit.
        commit_story_pass(
            repo_root=config.repo_root,
            story_id=story.id,
            story_title=story.title,
            session_id=session_id or "forge-python",
            iteration=iteration if iteration is not None else 1,
        )

        if mem is not None and session_id is not None:
            mem.end_iteration(session_id, iteration, story.id, "pass", "pass")
            mem.post_message(session_id, iteration, story.id, "STATUS",
                             f"[{story.id}] PASSED",
                             f"Story '{story.title}' passed all quality gates on iteration {iteration}.")
            mem.audit(session_id, iteration, story.id, "STORY_PASS", "prd.json", "")
            mem.audit(session_id, iteration, story.id, "GIT_COMMIT", "git", f"story={story.id}")
            if _closed_flag is not None:
                _closed_flag["done"] = True

        backlog_errors = check_backlog(config.prd_file, config.repo_root / "docs" / "AGENTIC_WORKLOG_LOOP.md", state_file)
        if backlog_errors:
            raise StoryExecutionError(f"Governance backlog gate failed:\n" + "\n".join(backlog_errors))

        close_errors = check_close(
            story.id,
            config.prd_file,
            progress_file,
            config.repo_root / "docs" / "AGENTIC_WORKLOG_LOOP.md",
        )
        if close_errors:
            raise StoryExecutionError(f"Governance close gate failed:\n" + "\n".join(close_errors))

        return RunOnceResult(
            story_id=story.id,
            status="pass",
            gates_result=gate_result.returncode,
            backend_result=None,
            archive_result=archive_result,
        )

    if mem is not None and session_id is not None:
        mem.end_iteration(session_id, iteration, story.id, "fail", "fail")
        mem.audit(
            session_id,
            iteration,
            story.id,
            "QUALITY_GATES_FAIL",
            str(gates_file),
            f"exit_code={gate_result.returncode}",
        )
        mem.audit(session_id, iteration, story.id, "STORY_FAIL", "prd.json", "")
        mem.post_message(session_id, iteration, story.id, "WARNING",
                         f"[{story.id}] Gates failed iter {iteration}",
                         "Quality gates returned non-zero. "
                         f"Exit code: {gate_result.returncode}\n"
                         f"stdout:\n{_output_snippet(gate_result.stdout)}\n"
                         f"stderr:\n{_output_snippet(gate_result.stderr)}\n"
                         "Story NOT passing. Next iteration will retry.")
        if _closed_flag is not None:
            _closed_flag["done"] = True

    append_progress(progress_file, story, "fail", session_id=session_id, iteration=iteration)
    return RunOnceResult(
        story_id=story.id,
        status="fail",
        gates_result=gate_result.returncode,
        backend_result=None,
        archive_result=archive_result,
    )


def run_main(
    config: ForgeConfig,
    source_root: Path,
    max_iterations: int | None = None,
    agent_command: str | None = None,
) -> RunMainResult:
    """Python-owned orchestration main loop.

    Mirrors forge.sh main(): initial UAP gate check, per-iteration story
    execution, and mission-close for complete / no-story / max-iterations
    outcomes.

    Source of truth: forge.sh lines 562-595 (uap_gate) and 878-915 (main loop).
    """
    if max_iterations is None:
        max_iterations = int(os.environ.get("FORGE_MAX_ITERATIONS", "3"))

    previous_iteration = os.environ.get("FORGE_ITERATION")
    env_state = _bootstrap_run_env(max_iterations)
    session_id = env_state.session_id

    # ── Main loop ──────────────────────────────────────────────────────────
    # run_once() handles the "no story remaining" UAP gate internally:
    # it runs final lint, emits COMPLETE, and closes the session as complete.
    try:
        for i in range(1, max_iterations + 1):
            os.environ["FORGE_ITERATION"] = str(i)
            result = run_once(config, source_root, agent_command)
            if result.status in {"complete", "lint_failed"}:
                return RunMainResult(
                    status=result.status,
                    iterations_used=i,
                    remaining_story_ids=[],
                )
            if result.status in {"fail", "timeout"}:
                # Mirrors forge.sh main() line 916: log retry warning and continue.
                print(
                    f"[FORGE] Story execution did not complete or pass gates"
                    f" (iter {i}). Retrying on next iteration.",
                    flush=True,
                )
            # "pass", "fail", "timeout": continue to next iteration

        # ── Max iterations exhausted — close as paused ─────────────────────
        prd = load_prd(config.prd_file)
        remaining_ids = [s.id for s in prd.stories() if not s.passes]
        iteration = int(os.environ.get("FORGE_ITERATION", str(max_iterations)))

        db_path = config.workspace_dir / "forge-memory.db"
        if db_path.exists():
            mem = ForgeMemory(db_path)
            mem.close_session(session_id, "paused")
            remaining_str = ", ".join(remaining_ids)
            mem.post_message(
                session_id,
                iteration,
                None,
                "WARNING",
                "Max iterations reached",
                f"{remaining_str} still incomplete. Increase max_iterations or split stories.",
            )
            trigger_sic(mem, session_id, config.repo_root)

        return RunMainResult(
            status="paused",
            iterations_used=max_iterations,
            remaining_story_ids=remaining_ids,
        )
    finally:
        _restore_run_env(env_state, previous_iteration)

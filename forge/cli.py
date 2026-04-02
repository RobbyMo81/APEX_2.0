from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .gates import ManualGateOwnershipError, generate_gates
from .governance import main_check_backlog, main_check_close, main_check_story
from .logging import info
from .preflight import PreflightError, run_preflight
from .prd_validator import main as prd_validator_main
from .runner import run_main, run_once
from .worklog_loop import run_worklog_loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge",
        description=(
            "Forge Python CLI skeleton for workspace inspection "
            "and migration-safe command dispatch."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Show resolved Forge configuration.",
    )
    inspect_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Validate repo root and workspace resolution.",
    )
    bootstrap_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    subparsers.add_parser(
        "generate-gates",
        help="Generate Forge-managed quality gates from the Python implementation.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the full Python orchestration loop (UAP gate + story iterations + mission close).",
    )
    run_parser.add_argument(
        "--agent-command",
        required=False,
        help="Optional shell command template override for the selected story.",
    )
    run_parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        help="Optional backend override. Defaults to FORGE_AGENT_BACKEND or 'claude'.",
    )
    run_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override FORGE_MAX_ITERATIONS. Defaults to the environment variable or 3.",
    )

    run_once_parser = subparsers.add_parser(
        "run-once",
        help="Execute one controlled Python runner iteration for migration validation.",
    )
    run_once_parser.add_argument(
        "--agent-command",
        required=False,
        help=(
            "Optional shell command template override for the selected story. "
            "Supports {story_id} and {story_title}."
        ),
    )
    run_once_parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        help="Optional backend override. Defaults to FORGE_AGENT_BACKEND or 'claude'.",
    )

    run_worklog_parser = subparsers.add_parser(
        "run-worklog",
        help="Run the agentic loop against a YAML-frontmatter markdown worklog.",
    )
    run_worklog_parser.add_argument(
        "--file",
        required=True,
        help="Path to the worklog markdown file.",
    )
    run_worklog_parser.add_argument(
        "--work-command",
        required=True,
        help=(
            "Shell command to execute todo work. Supports {todo_id}, {todo_title}, {worklog_path}."
        ),
    )
    run_worklog_parser.add_argument(
        "--verify-command",
        required=True,
        help=(
            "Shell verification command. Todo is marked done only when this exits 0. "
            "Supports {todo_id}, {todo_title}, {worklog_path}."
        ),
    )
    run_worklog_parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory used for work and verify commands.",
    )
    run_worklog_parser.add_argument(
        "--max-cycles",
        type=int,
        default=200,
        help="Safety cap for loop iterations.",
    )

    subparsers.add_parser(
        "preflight",
        help="Run Python-owned preflight checks (backend CLI, runtime binaries, PRD/prompt files, gitignore).",
    )

    check_story_parser = subparsers.add_parser(
        "check-story",
        help="Validate a PRD story against the governance schema rules.",
    )
    check_story_parser.add_argument("story_id", help="Story ID to validate (e.g. V2-026)")
    check_story_parser.add_argument(
        "--prd",
        default="prd.json",
        help="Path to the PRD JSON file (default: prd.json)",
    )

    check_close_parser = subparsers.add_parser(
        "check-close",
        help="Validate story closure evidence: PASS record in progress.txt and status/passes consistency.",
    )
    check_close_parser.add_argument("story_id", help="Story ID to validate (e.g. V2-027)")
    check_close_parser.add_argument(
        "--prd",
        default="prd.json",
        help="Path to the PRD JSON file (default: prd.json)",
    )
    check_close_parser.add_argument(
        "--progress",
        default=".forge/progress.txt",
        help="Path to the progress log file (default: .forge/progress.txt)",
    )

    check_backlog_parser = subparsers.add_parser(
        "check-backlog",
        help="Detect unauthorised new stories or ID/title mutations in prd.json.",
    )
    check_backlog_parser.add_argument(
        "--prd",
        default="prd.json",
        help="Path to the PRD JSON file (default: prd.json)",
    )
    check_backlog_parser.add_argument(
        "--worklog",
        default="docs/AGENTIC_WORKLOG_LOOP.md",
        help="Path to the append-only worklog file (default: docs/AGENTIC_WORKLOG_LOOP.md)",
    )
    check_backlog_parser.add_argument(
        "--state",
        default=".forge/governance_state.json",
        help="Path to the governance fingerprint state file (default: .forge/governance_state.json)",
    )

    # V2-040: Python-owned dispatch for legacy command surfaces
    commit_rules_parser = subparsers.add_parser(
        "commit-rules",
        help=(
            "Apply staged rules to FORGE_RULES.md via the TypeScript forge-memory-client. "
            "Python-owned dispatch; TypeScript is the implementation substrate."
        ),
    )
    commit_rules_parser.add_argument(
        "commit_rules_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded verbatim to forge-memory-client.ts commit-rules.",
    )

    subparsers.add_parser(
        "config",
        help=(
            "Launch the Forge config TUI (Rust binary). "
            "Python-owned dispatch; Rust is the implementation."
        ),
    )

    # V2-044: Raw PRD validation and handoff gate
    validate_prd_parser = subparsers.add_parser(
        "validate-prd",
        help=(
            "Validate a raw PRD markdown file against the required authoring structure "
            "and optionally verify the governed prd.json handoff output."
        ),
    )
    validate_prd_parser.add_argument(
        "raw_prd",
        help="Path to the raw PRD markdown file to validate.",
    )
    validate_prd_parser.add_argument(
        "--prd-out",
        default=None,
        help=(
            "Path to the governed prd.json output. When provided, runs the full handoff gate "
            "(raw PRD structure + governed prd.json contract validation). "
            "When omitted, validates raw PRD structure only."
        ),
    )

    return parser


def emit_config(as_json: bool) -> int:
    config = load_config()
    payload = config.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}={value}")
    return 0


def bootstrap(as_json: bool) -> int:
    config = load_config()
    payload = {
        **config.to_dict(),
        "workspace_exists": str(config.workspace_dir.exists()).lower(),
        "bash_runtime_exists": str(config.bash_runtime.exists()).lower(),
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        info(f"repo_root={config.repo_root}")
        info(f"workspace_dir={config.workspace_dir}")
        info(f"bash_runtime={config.bash_runtime}")
        info(f"workspace_exists={config.workspace_dir.exists()}")
        info(f"bash_runtime_exists={config.bash_runtime.exists()}")
    return 0


def run_generate_gates() -> int:
    config = load_config()
    source_root = Path(__file__).resolve().parents[1]
    try:
        gates_file, result = generate_gates(
            repo_root=config.repo_root,
            workspace_dir=config.workspace_dir,
            source_root=source_root,
        )
    except ManualGateOwnershipError as error:
        info(str(error))
        return 0

    info(f"Forge-managed quality gates refreshed at {gates_file}")
    info(f"detected_toolchains={','.join(result.toolchains)}")
    return 0


def run_main_command(
    agent_command: str | None,
    backend: str | None,
    max_iterations: int | None,
) -> int:
    config = load_config()
    if backend is not None:
        config = replace(config, agent_backend=backend)
    source_root = Path(__file__).resolve().parents[1]
    result = run_main(
        config=config,
        source_root=source_root,
        max_iterations=max_iterations,
        agent_command=agent_command,
    )
    info(f"run_main_status={result.status}")
    info(f"iterations_used={result.iterations_used}")
    if result.remaining_story_ids:
        info(f"remaining={','.join(result.remaining_story_ids)}")
    return 0 if result.status == "complete" else 1


def run_once_command(agent_command: str | None, backend: str | None) -> int:
    config = load_config()
    if backend is not None:
        config = replace(config, agent_backend=backend)
    source_root = Path(__file__).resolve().parents[1]
    result = run_once(config=config, source_root=source_root, agent_command=agent_command)
    info(f"run_once_status={result.status}")
    info(f"agent_backend={config.agent_backend}")
    if result.story_id is not None:
        info(f"story_id={result.story_id}")
    if result.gates_result is not None:
        info(f"gates_result={result.gates_result}")
    if result.backend_result is not None:
        info(f"backend_result={result.backend_result}")
    return 0 if result.status in {"pass", "complete"} else 1


def run_preflight_command() -> int:
    config = load_config()
    try:
        run_preflight(config)
    except PreflightError as exc:
        info(f"PREFLIGHT FAILED: {exc}")
        return 1
    info("Preflight passed.")
    return 0


def run_worklog_command(
    file_path: str,
    work_command: str,
    verify_command: str,
    cwd: str,
    max_cycles: int,
) -> int:
    worklog_path = Path(file_path).expanduser().resolve()
    command_cwd = Path(cwd).expanduser().resolve()

    result = run_worklog_loop(
        worklog_path=worklog_path,
        work_command=work_command,
        verify_command=verify_command,
        cwd=command_cwd,
        max_cycles=max_cycles,
    )
    info(f"worklog_status={result.status}")
    info(f"completed_todos={result.completed_todos}/{result.total_todos}")
    info(result.message)
    return 0 if result.status == "awaiting_instructions" else 1


def run_commit_rules_command(extra_args: list[str]) -> int:
    """Python-owned dispatch for commit-rules; delegates to TypeScript forge-memory-client.ts.

    Outcome: Python-owned. Delegated Python target: forge-memory-client.ts commit-rules.
    The TypeScript implementation is the authoritative substrate for rule-staging logic
    (V2-009). Python owns the entry-point dispatch surface.
    """
    config = load_config()
    repo_root = config.repo_root
    cmd = [
        "npx",
        "ts-node",
        "--project",
        "tsconfig.json",
        "forge-memory-client.ts",
        "commit-rules",
        *extra_args,
    ]
    result = subprocess.run(cmd, cwd=str(repo_root))
    return result.returncode


def run_config_command() -> int:
    """Python-owned dispatch for config; delegates to the Rust forge-config binary.

    Outcome: Python-owned. Delegated Python target: tools/config-gui/target/release/forge-config.
    The Rust binary (V2-016) is the authoritative TUI implementation.
    Python owns the entry-point dispatch surface and handles the optional build step.
    """
    config = load_config()
    repo_root = config.repo_root
    binary = repo_root / "tools" / "config-gui" / "target" / "release" / "forge-config"
    if not binary.exists():
        info("Building config GUI...")
        build_result = subprocess.run(
            ["npm", "run", "config:build", "--silent"], cwd=str(repo_root)
        )
        if build_result.returncode != 0:
            info("FORGE config build failed.")
            return build_result.returncode
    result = subprocess.run([str(binary)], cwd=str(repo_root))
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "inspect":
        return emit_config(as_json=args.json)
    if args.command == "bootstrap":
        return bootstrap(as_json=args.json)
    if args.command == "preflight":
        return run_preflight_command()
    if args.command == "generate-gates":
        return run_generate_gates()
    if args.command == "run":
        return run_main_command(args.agent_command, args.backend, args.max_iterations)
    if args.command == "run-once":
        return run_once_command(args.agent_command, args.backend)
    if args.command == "run-worklog":
        return run_worklog_command(
            file_path=args.file,
            work_command=args.work_command,
            verify_command=args.verify_command,
            cwd=args.cwd,
            max_cycles=args.max_cycles,
        )
    if args.command == "check-story":
        return main_check_story([args.story_id, "--prd", args.prd])
    if args.command == "check-close":
        return main_check_close([args.story_id, "--prd", args.prd, "--progress", args.progress])
    if args.command == "check-backlog":
        return main_check_backlog(["--prd", args.prd, "--worklog", args.worklog, "--state", args.state])
    if args.command == "commit-rules":
        return run_commit_rules_command(list(args.commit_rules_args))
    if args.command == "config":
        return run_config_command()
    if args.command == "validate-prd":
        argv_prd = [args.raw_prd]
        if args.prd_out:
            argv_prd += ["--prd-out", args.prd_out]
        return prd_validator_main(argv_prd)

    parser.print_help(sys.stderr)
    return 1

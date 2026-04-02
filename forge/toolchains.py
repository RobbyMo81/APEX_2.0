from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DetectionResult:
    toolchains: tuple[str, ...]
    gate_commands: tuple[str, ...]
    notes: tuple[str, ...]


APP_SOURCE_SIGNALS: tuple[str, ...] = (
    "src",
    "app",
    "server",
    "pages",
    "main.py",
    "manage.py",
    "go.mod",
    "Cargo.toml",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "index.ts",
    "index.js",
)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def load_package_json(repo_root: Path) -> dict[str, object]:
    package_path = repo_root / "package.json"
    if not package_path.exists():
        return {}
    loaded: object = json.loads(package_path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    return {}


def package_has_script(repo_root: Path, script_name: str) -> bool:
    package_data = load_package_json(repo_root)
    scripts = package_data.get("scripts")
    return isinstance(scripts, dict) and script_name in scripts


def package_name(repo_root: Path) -> str:
    package_data = load_package_json(repo_root)
    name = package_data.get("name", "")
    return name if isinstance(name, str) else ""


def package_description(repo_root: Path) -> str:
    package_data = load_package_json(repo_root)
    description = package_data.get("description", "")
    return description if isinstance(description, str) else ""


def repo_has_app_source_signals(repo_root: Path) -> bool:
    return any((repo_root / signal).exists() for signal in APP_SOURCE_SIGNALS)


def is_runtime_scaffold_package(repo_root: Path) -> bool:
    return (
        (repo_root / "package.json").exists()
        and package_name(repo_root) == "forgemp"
        and package_description(repo_root) == "FORGE autonomous build loop"
    )


def has_python_project(repo_root: Path) -> bool:
    markers = (
        "requirements.txt",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.py",
        "conftest.py",
    )
    if any((repo_root / marker).exists() for marker in markers):
        return True
    for path in repo_root.rglob("*.py"):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        return True
    return False


def detect_toolchains(repo_root: Path) -> DetectionResult:
    toolchains: list[str] = []
    gate_commands: list[str] = []
    notes: list[str] = []

    using_runtime_scaffold = False
    if is_runtime_scaffold_package(repo_root) and not repo_has_app_source_signals(repo_root):
        using_runtime_scaffold = True
        _append_unique(
            notes,
            (
                "Detected Forge runtime scaffold package.json without app source signals; "
                "skipping Node package scripts."
            ),
        )

    if (repo_root / "package.json").exists() and not using_runtime_scaffold:
        _append_unique(toolchains, "node")
        if package_has_script(repo_root, "typecheck"):
            _append_unique(gate_commands, "npm run typecheck")
        elif (repo_root / "tsconfig.json").exists():
            _append_unique(gate_commands, "npx tsc --noEmit")
            _append_unique(
                notes,
                "No package typecheck script found; using TypeScript compiler fallback.",
            )
        if package_has_script(repo_root, "lint"):
            _append_unique(gate_commands, "npm run lint")
        if package_has_script(repo_root, "test"):
            _append_unique(gate_commands, "npm test")
        if package_has_script(repo_root, "build"):
            _append_unique(gate_commands, "npm run build")

    if (repo_root / "Cargo.toml").exists():
        _append_unique(toolchains, "rust")
        _append_unique(gate_commands, "cargo test --quiet")

    if (repo_root / "go.mod").exists():
        _append_unique(toolchains, "go")
        _append_unique(gate_commands, "go test ./...")

    if has_python_project(repo_root):
        _append_unique(toolchains, "python")
        if (repo_root / ".venv" / "bin" / "pytest").exists():
            _append_unique(gate_commands, ".venv/bin/pytest -q")
        else:
            _append_unique(gate_commands, "python3 -m pytest -q")

    if not gate_commands:
        _append_unique(toolchains, "fallback")
        _append_unique(
            notes,
            "No recognized app toolchains detected; using safe structural fallback gates.",
        )

    return DetectionResult(
        toolchains=tuple(toolchains),
        gate_commands=tuple(gate_commands),
        notes=tuple(notes),
    )

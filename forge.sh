#!/usr/bin/env bash
# ============================================================
# FORGE — Autonomous Agent Loop for Kirk's Build Systems
# Inspired by Ralph (snarktank/ralph), engineered for the stack.
#
# Memory Architecture:
#   forge-memory.db — SQLite stateful working directory (PRIMARY)
#   progress.txt    — Human-readable append-only log (SECONDARY)
#   prd.json        — Story task list and passes flags
#   git history     — Code audit trail
#
# GOVERNANCE: MEMORY_PROTOCOL.md is law. DB must exist and pass
# health check before any agent runs. No exceptions.
# ============================================================

set -euo pipefail

# ── Config ──────────────────────────────────────────────────
MAX_ITERATIONS="${1:-10}"
CLAUDE_TIMEOUT_SECONDS="${CLAUDE_TIMEOUT_SECONDS:-300}"
PRD_FILE="prd.json"
PROGRESS_FILE="progress.txt"
PROMPT_FILE="$(dirname "$0")/prompt.md"
MEMORY_SH="$(dirname "$0")/forge-memory.sh"
ARCHIVE_DIR="archive"
export FORGE_DB="${FORGE_DB_PATH:-forge-memory.db}"

# ── Session ID — unique per run ────────────────────────────
SESSION_ID="forge-$(date +%Y%m%d%H%M%S)-$$"
CURRENT_ITERATION=0

# ── Color output ────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()    { echo -e "${CYAN}[FORGE]${RESET} $*"; }
ok()     { echo -e "${GREEN}[FORGE ✓]${RESET} $*"; }
warn()   { echo -e "${YELLOW}[FORGE ⚠]${RESET} $*"; }
fail()   { echo -e "${RED}[FORGE ✗]${RESET} $*"; exit 1; }

# ── Source memory layer ─────────────────────────────────────
[[ -f "$MEMORY_SH" ]] || fail "forge-memory.sh not found at: $MEMORY_SH — cannot start without memory layer."
# shellcheck source=forge-memory.sh
source "$MEMORY_SH"

# ── Preflight checks ────────────────────────────────────────
preflight() {
  log "Running preflight checks..."
  command -v claude  &>/dev/null || fail "Claude Code CLI not found. Install: https://docs.claude.com/claude-code"
  command -v jq      &>/dev/null || fail "jq not found. brew install jq / apt install jq"
  command -v sqlite3 &>/dev/null || fail "sqlite3 not found. brew install sqlite / apt install sqlite3"
  command -v git     &>/dev/null || fail "git not found."
  [[ -f "$PRD_FILE" ]]           || fail "prd.json not found. Load the forge skill to generate it."
  [[ -f "$PROMPT_FILE" ]]        || fail "prompt.md not found at: $PROMPT_FILE"

  # Enforce DB in gitignore
  if [[ -f ".gitignore" ]] && ! grep -q "forge-memory.db" .gitignore 2>/dev/null; then
    printf "\n# FORGE memory DB — runtime working directory, not source of record\nforge-memory.db\nforge-memory.db-shm\nforge-memory.db-wal\nforge-startup-report.md\n" >> .gitignore
    ok ".gitignore updated with forge-memory.db entries"
  fi

  ok "Preflight passed."
}

# ── Memory Init ─────────────────────────────────────────────
init_memory() {
  memory_init           # Create schema if new
  memory_health_check   # Verify schema version — HALTS if wrong

  local branch_name project_name
  branch_name=$(jq -r '.branchName // "forge/feature"' "$PRD_FILE")
  project_name=$(jq -r '.projectName // "unknown"' "$PRD_FILE")

  memory_create_session "$SESSION_ID" "$branch_name" "$project_name" "$MAX_ITERATIONS"
  memory_set_context "session_id"   "$SESSION_ID"  "global" "text" "forge.sh"
  memory_set_context "branch_name"  "$branch_name" "global" "text" "forge.sh"
  memory_set_context "project_name" "$project_name" "global" "text" "forge.sh"
  memory_set_context "forge_db"     "$FORGE_DB"    "global" "path" "forge.sh"

  # ── STARTUP REPORT: generates forge-startup-report.md + prints to terminal
  memory_print_startup_report "$SESSION_ID" "$project_name" "$branch_name"

  # ── SIDECARS: Launch and wait for health
  sidecars_init
}

declare -A SIDECAR_PIDS=()

# ── Sidecar Orchestration (V2.1) ───────────────────────────
python_venv_init() {
  if [[ -f "requirements.txt" ]]; then
    if [[ ! -d ".venv" ]]; then
      log "requirements.txt found but .venv missing. Bootstrapping Python virtual environment..."
      python3 -m venv .venv || fail "Failed to create Python virtual environment."
      log "Installing dependencies from requirements.txt (timeout=120s)..."
      # Safety gate: pip install timeout — prevents indefinite hang on slow/broken packages
      if command -v timeout &>/dev/null; then
        timeout 120 .venv/bin/pip install --quiet -r requirements.txt || fail "pip install failed or timed out (120s limit)."
      else
        .venv/bin/pip install --quiet -r requirements.txt || fail "Failed to install Python dependencies."
      fi
      ok "Python virtual environment ready."
    else
      log "Python .venv already exists. Skipping bootstrap."
    fi
  fi
}

sidecars_init() {
  local count
  count=$(jq '.sidecars | length' "$PRD_FILE" 2>/dev/null || echo "0")
  [[ "$count" -eq 0 ]] && return

  # Initialize Python Venv if needed (Story V2-006)
  python_venv_init

  log "Initializing $count sidecar(s)..."

  # 1. Dependency-ordered startup
  # Logic: Start Level 0 (no depends_on), then Level 1, etc.
  local level=0
  local max_levels=5
  local started_ids=""

  while [[ $level -lt $max_levels ]]; do
    local level_started=false
    
    # Get sidecars for this level
    local ids
    ids=$(jq -r --argjson started "${started_ids:-[]}" '
      .sidecars[] | 
      select(.depends_on == null or .depends_on == [] or (.depends_on - $started == [])) | 
      select( .id as $id | any($started[]; . == $id) | not ) | 
      .id' "$PRD_FILE")

    for id in $ids; do
      local cmd sidecar_type cwd timeout interval use_venv
      cmd=$(jq -r ".sidecars[] | select(.id==\"$id\") | .command" "$PRD_FILE")
      sidecar_type=$(jq -r ".sidecars[] | select(.id==\"$id\") | .type // \"unknown\"" "$PRD_FILE")
      cwd=$(jq -r ".sidecars[] | select(.id==\"$id\") | .cwd // \".\"" "$PRD_FILE")
      timeout=$(jq -r ".sidecars[] | select(.id==\"$id\") | .startup_timeout_sec // 30" "$PRD_FILE")
      interval=$(jq -r ".sidecars[] | select(.id==\"$id\") | .heartbeat_interval_sec // 15" "$PRD_FILE")
      use_venv=$(jq -r ".sidecars[] | select(.id==\"$id\") | .use_venv // false" "$PRD_FILE")

      # Process isolation check: warn if launching sidecars as root
      if [[ $(id -u) -eq 0 ]]; then
        warn "SAFETY [Process Isolation]: FORGE is running as root. Sidecar [$id] will inherit root privileges — process isolation is weakened."
      fi

      log "Starting sidecar [$id] (type=$sidecar_type cmd=$cmd venv=$use_venv)..."
      
      # Launch in background with environment
      # Extract env vars from JSON and export them
      local env_json
      env_json=$(jq -c ".sidecars[] | select(.id==\"$id\") | .env // {}" "$PRD_FILE")
      
      ( 
        cd "$cwd"
        # Export each key/value from the env_json
        while read -r key value; do
          export "$key=$value"
        done < <(echo "$env_json" | jq -r 'to_entries[] | "\(.key) \(.value)"')
        
        # Story V2-006: Activate Venv if requested
        if [[ "$use_venv" == "true" && -f ".venv/bin/activate" ]]; then
          source .venv/bin/activate
        fi

        # Log to tmp file
        mkdir -p tmp/sidecars
        eval "$cmd" > "tmp/sidecars/$id.log" 2>&1
      ) &
      SIDECAR_PIDS["$id"]=$!
      
      # 2. Wait for initial HEARTBEAT
      local start_time=$(date +%s)
      local healthy=false
      while [[ $(($(date +%s) - start_time)) -lt "$timeout" ]]; do
        if memory_check_sidecar "$id" "$((interval * 2))"; then
          healthy=true
          break
        fi
        sleep 1
      done

      if [[ "$healthy" == "true" ]]; then
        ok "Sidecar [$id] is HEALTHY."
        # Append to started_ids as a JSON array string
        if [[ -z "$started_ids" || "$started_ids" == "[]" ]]; then
          started_ids="[\"$id\"]"
        else
          started_ids=$(echo "$started_ids" | jq -c ". + [\"$id\"]")
        fi
        level_started=true
      else
        fail "Sidecar [$id] failed to start or miss heartbeat within ${timeout}s."
      fi
    done

    [[ "$level_started" == "false" ]] && break
    level=$((level + 1))
  done

  # Verify all mandatory sidecars are up
  local missing
  missing=$(jq -r --argjson started "${started_ids:-[]}" '
    .sidecars[] | select(.mandatory == true) | select(.id as $id | any($started[]; . == $id) | not) | .id' "$PRD_FILE")
  
  if [[ -n "$missing" ]]; then
    fail "Mandatory sidecars failed to start: $missing"
  fi
}

sidecars_reap() {
  [[ ${#SIDECAR_PIDS[@]} -eq 0 ]] && return
  
  log "Reaping ${#SIDECAR_PIDS[@]} sidecar(s)..."
  
  # SIGTERM first
  for id in "${!SIDECAR_PIDS[@]}"; do
    local pid="${SIDECAR_PIDS[$id]}"
    if kill -0 "$pid" 2>/dev/null; then
      log "Sending SIGTERM to sidecar [$id] (PID $pid)..."
      kill "$pid" 2>/dev/null || true
    fi
  done

  # Wait 10s
  log "Waiting 10s for graceful exit..."
  sleep 10

  # SIGKILL remainders
  for id in "${!SIDECAR_PIDS[@]}"; do
    local pid="${SIDECAR_PIDS[$id]}"
    if kill -0 "$pid" 2>/dev/null; then
      warn "Sidecar [$id] (PID $pid) still alive. Sending SIGKILL."
      kill -9 "$pid" 2>/dev/null || true
    fi
  done

  # Process verification: confirm all sidecars are dead
  sleep 1
  local surviving=""
  for id in "${!SIDECAR_PIDS[@]}"; do
    local pid="${SIDECAR_PIDS[$id]}"
    if kill -0 "$pid" 2>/dev/null; then
      surviving="$surviving $id(PID=$pid)"
    fi
  done
  if [[ -n "$surviving" ]]; then
    warn "SAFETY [Process Isolation]: Sidecars still alive after SIGKILL:$surviving"
  else
    ok "Process verification: all sidecars confirmed dead."
  fi

  ok "All sidecars reaped."
}

# ── GitHub Failure Cycle (V2.4) ─────────────────────────────
trigger_github_cycle() {
  log "[GITHUB CYCLE] Terminal failure detected. Phoning home..."
  
  # Check if gh is authenticated
  if ! gh auth status &>/dev/null; then
    warn "[GITHUB CYCLE] Skipping: gh CLI not authenticated."
    return
  fi

  # Record trigger in audit_log for the github-notifier sidecar
  memory_audit "$SESSION_ID" "" "github-notifier" "TRIGGER_GITHUB_CYCLE" "terminal_failure" "status=failed"
  
  ok "[GITHUB CYCLE] Notification dispatched to sidecar."
}

# ── Self-Improvement Cycle Trigger (V2.2 SIC) ──────────────
trigger_sic() {
  log "Gating Self-Improvement Cycle (SIC)..."
  
  # 1. Telemetry Gate: iterations > 0
  local iter_count
  iter_count=$(memory_query "SELECT count(*) FROM agent_iterations WHERE session_id='$SESSION_ID';" 2>/dev/null || echo "0")
  
  if [[ "$iter_count" -eq 0 ]]; then
    warn "SIC Skipped: INSUFFICIENT_TELEMETRY (0 iterations found for session $SESSION_ID)"
    return
  fi

  # 2. Run Refiner & Stage
  log "Running ForgeRefiner for session $SESSION_ID..."
  npx ts-node --project tsconfig.json forge-memory-client.ts stage "$SESSION_ID"
  
  local score mode
  score=$(jq -r '.shs.score' "staged-rules/session_$SESSION_ID/manifest.json")
  mode=$(jq -r '.shs.mode' "staged-rules/session_$SESSION_ID/manifest.json")
  
  ok "SIC Analysis Complete. SHS: $score | Mode: $mode"
  memory_audit "$SESSION_ID" "" "" "SIC_TRIGGER" "ForgeRefiner" "score=$score mode=$mode"

  # 3. GitHub Cycle (V2.4)
  # Trigger on Audit mode (SHS < 40) or terminal failure status
  local session_status
  session_status=$(memory_query "SELECT status FROM forge_sessions WHERE id='$SESSION_ID';" 2>/dev/null || echo "unknown")

  if [[ "$mode" == "AUDIT" || "$session_status" == "failed" || "$session_status" == "paused" ]]; then
    trigger_github_cycle
  fi
}

# ── Function 0: UAP Self-Review Gate ────────────────────────
uap_gate() {
  log "Function 0 — UAP Self-Review Gate"

  local all_pass
  all_pass=$(jq '[.userStories[] | select(.passes == false)] | length' "$PRD_FILE")

  if [[ "$all_pass" -eq 0 ]]; then
    log "All stories marked as passed. Running final quality gate: LINT..."
    
    if ! npm run lint --silent; then
      warn "Final linting failed. Codebase must be clean before mission completion."
      memory_post_message "$SESSION_ID" "0" "" "BLOCKER" \
        "Final Linting Failed" "All stories passed but 'npm run lint' failed. Fix lint errors to close the session."
      return 1
    fi

    ok "Final linting passed."
    memory_close_session "$SESSION_ID" "complete"
    memory_post_message "$SESSION_ID" "0" "" "STATUS" \
      "All stories complete" "All PRD stories passed and linting verified. Session closed cleanly."
    
    # Trigger SIC after finalization
    trigger_sic

    ok "All stories pass. Mission complete."
    echo -e "${GREEN}${BOLD}<promise>COMPLETE</promise>${RESET}"
    exit 0
  fi

  local branch_name
  branch_name=$(jq -r '.branchName // "forge/feature"' "$PRD_FILE")
  log "Branch: $branch_name | Remaining stories: $all_pass"
  memory_audit "$SESSION_ID" "" "" "UAP_GATE_PASS" "prd.json" "remaining=$all_pass"
}

# ── Archive prior run if branch changed ─────────────────────
archive_if_needed() {
  local current_branch
  current_branch=$(jq -r '.branchName // ""' "$PRD_FILE")
  local last_branch=""
  [[ -f .forge_last_branch ]] && last_branch=$(cat .forge_last_branch)

  if [[ -n "$last_branch" && "$last_branch" != "$current_branch" ]]; then
    local archive_path="$ARCHIVE_DIR/$(date +%Y-%m-%d)-${last_branch//\//-}"
    warn "Branch changed. Archiving previous run to $archive_path"
    mkdir -p "$archive_path"
    [[ -f progress.txt ]] && cp progress.txt "$archive_path/"
    [[ -f prd.json ]]     && cp prd.json "$archive_path/"
    memory_archive "$archive_path"
    > progress.txt
    ok "Archived to: $archive_path"
    memory_audit "$SESSION_ID" "" "" "ARCHIVE" "$archive_path" "branch_change: $last_branch → $current_branch"
  fi

  echo "$current_branch" > .forge_last_branch
}

# ── Branch setup ────────────────────────────────────────────
ensure_branch() {
  local branch_name
  branch_name=$(jq -r '.branchName // "forge/feature"' "$PRD_FILE")

  if ! git show-ref --verify --quiet "refs/heads/$branch_name"; then
    log "Creating branch: $branch_name"
    git checkout -b "$branch_name"
    memory_audit "$SESSION_ID" "" "" "GIT_BRANCH_CREATE" "git" "branch=$branch_name"
  elif [[ "$(git branch --show-current)" != "$branch_name" ]]; then
    log "Switching to branch: $branch_name"
    git checkout "$branch_name"
    memory_audit "$SESSION_ID" "" "" "GIT_BRANCH_SWITCH" "git" "branch=$branch_name"
  fi
}

# ── Pick next story ─────────────────────────────────────────
pick_story() {
  jq -r '[.userStories[] | select(.passes == false)] | sort_by(.priority) | .[0]' "$PRD_FILE"
}

# ── Build agent context payload ─────────────────────────────
build_agent_context() {
  local story="$1"

  cat <<CONTEXT_EOF
$(cat "$PROMPT_FILE")

---
## FORGE SESSION CONTEXT

**Session ID:** ${SESSION_ID}
**Iteration:** ${CURRENT_ITERATION}
**Memory DB:** ${FORGE_DB}

Your primary briefing document is forge-startup-report.md — read it first (Function 0 requires this).
Use ForgeMemory (forge-memory-client.ts) for all entry/exit obligations.

---
## CURRENT STORY
$(echo "$story" | jq '.')

---
## PRD STATE
$(cat "$PRD_FILE")

---
## PROGRESS LOG
$(cat "$PROGRESS_FILE" 2>/dev/null || echo "(no prior progress)")

---
## MEMORY STARTUP REPORT
$(cat forge-startup-report.md 2>/dev/null || echo "(startup report not found — check forge-memory.sh ran cleanly)")
CONTEXT_EOF
}

# ── Run one Claude Code iteration ───────────────────────────
run_iteration() {
  local iteration="$1"
  local story="$2"
  local story_id story_title claude_exit

  story_id=$(echo "$story"    | jq -r '.id')
  story_title=$(echo "$story" | jq -r '.title')
  CURRENT_ITERATION=$iteration

  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "Iteration ${iteration}/${MAX_ITERATIONS}"
  log "Story: [${story_id}] ${story_title}"
  log "Session: ${SESSION_ID}"
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  memory_start_iteration "$SESSION_ID" "$iteration" "$story_id" "$story_title"

  local context
  context=$(build_agent_context "$story")

  export FORGE_SESSION_ID="$SESSION_ID"
  export FORGE_ITERATION="$iteration"
  export FORGE_STORY_ID="$story_id"

  if command -v timeout &>/dev/null; then
    echo "$context" | timeout "${CLAUDE_TIMEOUT_SECONDS}s" \
      claude --print --dangerously-skip-permissions -p "$(cat -)" 2>&1
    claude_exit=$?
  else
    warn "'timeout' command not found; Claude run will be unbounded."
    echo "$context" | claude --print --dangerously-skip-permissions -p "$(cat -)" 2>&1
    claude_exit=$?
  fi

  case "$claude_exit" in
    0)
      ok "Iteration ${iteration} complete."
      return 0
      ;;
    124)
      warn "Claude Code timed out after ${CLAUDE_TIMEOUT_SECONDS}s."
      memory_audit "$SESSION_ID" "$iteration" "$story_id" "CLAUDE_TIMEOUT" "claude" "timeout=${CLAUDE_TIMEOUT_SECONDS}s"
      return 124
      ;;
    *)
      warn "Claude Code returned non-zero exit (${claude_exit})."
      memory_audit "$SESSION_ID" "$iteration" "$story_id" "CLAUDE_NONZERO_EXIT" "claude" "exit_code=${claude_exit}"
      return "$claude_exit"
      ;;
  esac
}

# ── Run quality gates ────────────────────────────────────────
run_quality_gates() {
  local story_id="$1"
  local gates_file="forge.gates.sh"

  if [[ -f "$gates_file" ]]; then
    log "Running quality gates..."
    if bash "$gates_file"; then
      ok "Gates passed."
      memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "GATE_PASS" "forge.gates.sh" ""
      return 0
    else
      warn "Quality gates FAILED — story will not be marked passing."
      memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "GATE_FAIL" "forge.gates.sh" ""
      memory_post_message "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "WARNING" \
        "[${story_id}] Gates failed iter ${CURRENT_ITERATION}" \
        "Quality gates returned non-zero. Story NOT passing. Next iteration will retry."
      return 1
    fi
  else
    warn "No forge.gates.sh found — gates skipped. Add forge.gates.sh for enforcement."
    memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "GATE_SKIPPED" "forge.gates.sh" "file not found"
    return 0
  fi
}

# ── Mark story passing ──────────────────────────────────────
mark_story_passing() {
  local story="$1"
  local story_id story_title

  story_id=$(echo "$story"    | jq -r '.id')
  story_title=$(echo "$story" | jq -r '.title')

  # Update prd.json
  jq --arg id "$story_id" \
    '(.userStories[] | select(.id == $id) | .passes) = true' \
    "$PRD_FILE" > "${PRD_FILE}.tmp" && mv "${PRD_FILE}.tmp" "$PRD_FILE"

  # Update memory DB
  memory_end_iteration "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "pass" "pass"
  memory_post_message "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "STATUS" \
    "[${story_id}] PASSED" \
    "Story '${story_title}' passed all quality gates on iteration ${CURRENT_ITERATION}."
  memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "STORY_PASS" "prd.json" ""

  ok "Story [$story_id] marked passes: true"

  # Append to progress.txt
  {
    echo ""
    echo "[$(date -u +%Y-%m-%d)] Story [${story_id}]: ${story_title}"
    echo "STATUS: PASS | Session: ${SESSION_ID} | Iteration: ${CURRENT_ITERATION}"
    echo "---"
  } >> "$PROGRESS_FILE"

  # Git commit
  git add -A
  git commit -m "forge(${story_id}): ${story_title}

Session: ${SESSION_ID}
Iteration: ${CURRENT_ITERATION}" \
    --no-verify 2>/dev/null || warn "Nothing new to commit for this story."

  memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "GIT_COMMIT" "git" "story=${story_id}"
}

# ── Mark story failed ───────────────────────────────────────
mark_story_failed() {
  local story="$1"
  local story_id
  story_id=$(echo "$story" | jq -r '.id')

  memory_end_iteration "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "fail" "fail"
  memory_audit "$SESSION_ID" "$CURRENT_ITERATION" "$story_id" "STORY_FAIL" "prd.json" ""
}

# ── Cleanup on exit (normal, SIGINT, or SIGTERM) ────────────
# Single authoritative cleanup handler. Covers:
#   1. Sidecar reaping (always)
#   2. Session close on abnormal exit
# Must be defined AFTER sidecars_reap() to ensure it's callable.
cleanup() {
  local exit_code=$?
  sidecars_reap
  if [[ $exit_code -ne 0 ]]; then
    warn "FORGE exiting abnormally (code $exit_code). Closing session as 'failed'."
    memory_close_session "$SESSION_ID" "failed" 2>/dev/null || true
    memory_audit "$SESSION_ID" "" "" "ABNORMAL_EXIT" "forge.sh" "exit_code=$exit_code" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# ── Main ────────────────────────────────────────────────────
main() {
  # Handle commands (Story V2-009, V2-016)
  case "${1:-}" in
    "commit-rules")
      shift
      npx ts-node --project tsconfig.json forge-memory-client.ts commit-rules "$@"
      exit 0
      ;;
    "config")
      if [[ ! -f "tools/config-gui/target/release/forge-config" ]]; then
        log "Building config GUI..."
        npm run config:build --silent
      fi
      ./tools/config-gui/target/release/forge-config
      exit 0
      ;;
  esac

  echo ""
  echo -e "${BOLD}${CYAN}"
  echo "  ███████╗ ██████╗ ██████╗  ██████╗ ███████╗"
  echo "  ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝"
  echo "  █████╗  ██║   ██║██████╔╝██║  ███╗█████╗  "
  echo "  ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  "
  echo "  ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗"
  echo "  ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝"
  echo -e "${RESET}"
  log "Autonomous Build Loop — Kirk Edition"
  log "Session: ${SESSION_ID}"
  log "Max iterations: $MAX_ITERATIONS"
  echo ""

  # ════════════════════════════════════════════════════════
  # STARTUP SEQUENCE — ORDER IS GOVERNANCE-ENFORCED
  # Each step must succeed before the next runs.
  # See MEMORY_PROTOCOL.md for rationale.
  # ════════════════════════════════════════════════════════
  preflight         # 1. Validate tooling (sqlite3 required)
  init_memory       # 2. Init DB, health check, create session, print startup report
  archive_if_needed # 3. Archive if branch changed
  uap_gate          # 4. Exit if all stories done; else proceed
  ensure_branch     # 5. Switch/create git branch
  # ════════════════════════════════════════════════════════

  for ((i=1; i<=MAX_ITERATIONS; i++)); do
    local remaining
    remaining=$(jq '[.userStories[] | select(.passes == false)] | length' "$PRD_FILE")
    [[ "$remaining" -eq 0 ]] && {
      memory_close_session "$SESSION_ID" "complete"
      ok "All stories complete after ${i} iterations."
      echo -e "${GREEN}${BOLD}<promise>COMPLETE</promise>${RESET}"
      exit 0
    }

    local story
    story=$(pick_story)
    [[ -z "$story" || "$story" == "null" ]] && {
      memory_close_session "$SESSION_ID" "complete"
      ok "No more stories to process."
      exit 0
    }

    if run_iteration "$i" "$story"; then
      if run_quality_gates "$(echo "$story" | jq -r '.id')"; then
        mark_story_passing "$story"
      else
        mark_story_failed "$story"
        warn "Retrying story on next iteration."
      fi
    else
      mark_story_failed "$story"
      warn "Iteration did not complete successfully. Retrying story on next iteration."
    fi

    echo ""
  done

  memory_close_session "$SESSION_ID" "paused"
  warn "Max iterations (${MAX_ITERATIONS}) reached. Remaining stories:"
  jq '.userStories[] | select(.passes == false) | {id, title, priority}' "$PRD_FILE"
  memory_post_message "$SESSION_ID" "$CURRENT_ITERATION" "" "WARNING" \
    "Max iterations reached" \
    "$(jq -r '[.userStories[] | select(.passes == false) | .id] | join(", ")' "$PRD_FILE") still incomplete. Increase max_iterations or split stories."
  
  # Trigger SIC on exit
  trigger_sic
  
  exit 1
}

main "$@"

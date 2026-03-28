#!/usr/bin/env bash
# ============================================================
# FORGE MEMORY LAYER — forge-memory.sh
# SQLite-backed stateful working memory for agent teams.
# Sourced by forge.sh — do not run directly.
#
# GOVERNANCE: All agent-to-agent communication, context
# persistence, and audit trails flow through this database.
# No agent may claim to have no memory of prior work.
# ============================================================

FORGE_DB="${FORGE_DB_PATH:-forge-memory.db}"
FORGE_DB_VERSION="1"

# ── Concurrency Wrapper (V2.1) ──────────────────────────────
# Executes sqlite3 commands with exponential backoff on busy locks.
memory_query() {
  local query="$1"
  local max_retries=5
  local attempt=1
  local delay=0.1

  while [[ $attempt -le $max_retries ]]; do
    local output
    if output=$(sqlite3 -batch "$FORGE_DB" "$query" 2>&1); then
      [[ -n "$output" ]] && echo "$output"
      return 0
    fi

    if [[ "$output" == *"database is locked"* ]]; then
      warn "DB Locked (attempt $attempt/$max_retries). Retrying in ${delay}s..."
      sleep "$delay"
      attempt=$((attempt + 1))
      delay=$(echo "$delay * 2" | bc -l)
    else
      echo "$output" >&2
      return 1
    fi
  done

  fail "DB Lock timeout after $max_retries attempts: $query"
}

# ── Preflight ───────────────────────────────────────────────
memory_preflight() {
  command -v sqlite3 &>/dev/null || {
    echo -e "${RED}[FORGE-MEMORY ✗]${RESET} sqlite3 not found."
    echo -e "  Install: ${YELLOW}brew install sqlite${RESET} / ${YELLOW}apt install sqlite3${RESET}"
    exit 1
  }
}

# ── Schema Bootstrap ────────────────────────────────────────
memory_init() {
  memory_preflight

  sqlite3 "$FORGE_DB" <<'SQL'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── SESSIONS ─────────────────────────────────────────────
-- One row per forge.sh invocation
CREATE TABLE IF NOT EXISTS forge_sessions (
  id            TEXT PRIMARY KEY,
  started_at    TEXT NOT NULL,
  branch_name   TEXT NOT NULL,
  project_name  TEXT NOT NULL,
  max_iterations INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  completed_at  TEXT,
  CHECK(status IN ('running','complete','failed','paused'))
);

-- ── AGENT ITERATIONS ─────────────────────────────────────
-- One row per Claude Code instance spawned
CREATE TABLE IF NOT EXISTS agent_iterations (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES forge_sessions(id),
  iteration     INTEGER NOT NULL,
  story_id      TEXT NOT NULL,
  story_title   TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  gate_result   TEXT,
  started_at    TEXT NOT NULL,
  completed_at  TEXT,
  CHECK(status IN ('running','pass','fail','blocked')),
  CHECK(gate_result IS NULL OR gate_result IN ('pass','fail','skipped'))
);

-- ── AGENT MESSAGE BUS ─────────────────────────────────────
-- Inter-agent communication. Future agents READ unread messages.
CREATE TABLE IF NOT EXISTS agent_messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  from_session  TEXT NOT NULL,
  from_iter     INTEGER,
  story_id      TEXT,
  message_type  TEXT NOT NULL,
  subject       TEXT NOT NULL,
  body          TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  read_at       TEXT,
  CHECK(message_type IN ('DISCOVERY','BLOCKER','HANDOFF','WARNING','STATUS','DECISION'))
);

-- ── CONTEXT STORE ─────────────────────────────────────────
-- Persistent key/value pairs agents write for future agents.
-- Think: environment variables that survive context windows.
CREATE TABLE IF NOT EXISTS context_store (
  key           TEXT NOT NULL,
  scope         TEXT NOT NULL DEFAULT 'global', -- global | story:<id>
  value         TEXT NOT NULL,
  value_type    TEXT NOT NULL DEFAULT 'text',   -- text | json | path | url
  written_by    TEXT NOT NULL,
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (key, scope)
);

-- ── DISCOVERIES ───────────────────────────────────────────
-- Structured findings that feed into AGENTS.md auto-generation
CREATE TABLE IF NOT EXISTS discoveries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  story_id      TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  iteration     INTEGER NOT NULL,
  type          TEXT NOT NULL,
  title         TEXT NOT NULL,
  detail        TEXT NOT NULL,
  trigger_id    TEXT, -- V2.1
  payload_hash  TEXT, -- V2.1
  source        TEXT, -- V2.1
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  exported_to_agents_md INTEGER NOT NULL DEFAULT 0,
  CHECK(type IN ('PATTERN','GOTCHA','BLOCKER','DECISION','DEPENDENCY','CONVENTION','TRIGGER'))
);

-- ── STORY STATE ───────────────────────────────────────────
-- Extended story state beyond prd.json passes flag
CREATE TABLE IF NOT EXISTS story_state (
  story_id      TEXT PRIMARY KEY,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  blockers      TEXT,   -- JSON array of blocker strings
  context_notes TEXT,   -- free-form notes for next agent
  last_session  TEXT,
  last_updated  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── AUDIT LOG ─────────────────────────────────────────────
-- Immutable append-only record of every significant action
CREATE TABLE IF NOT EXISTS audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL,
  iteration     INTEGER,
  story_id      TEXT,
  action        TEXT NOT NULL,
  entity        TEXT,
  detail        TEXT,
  ts            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── DB META ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS db_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO db_meta VALUES ('schema_version', '1');
INSERT OR IGNORE INTO db_meta VALUES ('created_at', datetime('now'));
INSERT OR IGNORE INTO db_meta VALUES ('project', 'FORGE');
SQL

  log "Memory DB initialized: ${FORGE_DB}"
}

# ── Session Management ──────────────────────────────────────
memory_create_session() {
  local session_id="$1"
  local branch_name="$2"
  local project_name="$3"
  local max_iter="$4"

  memory_query \
    "INSERT INTO forge_sessions(id, started_at, branch_name, project_name, max_iterations, status)
     VALUES('$session_id', datetime('now'), '$branch_name', '$project_name', $max_iter, 'running');"

  memory_audit "$session_id" "" "" "SESSION_START" "forge_sessions" "branch=$branch_name project=$project_name"
}

memory_close_session() {
  local session_id="$1"
  local status="$2"  # complete | failed | paused

  memory_query \
    "UPDATE forge_sessions SET status='$status', completed_at=datetime('now') WHERE id='$session_id';"

  memory_audit "$session_id" "" "" "SESSION_END" "forge_sessions" "status=$status"
}

# ── Iteration Tracking ─────────────────────────────────────
memory_start_iteration() {
  local session_id="$1"
  local iteration="$2"
  local story_id="$3"
  local story_title="${4//\'/\'\'}"  # escape single quotes
  local iter_id="${session_id}-${iteration}"

  memory_query \
    "INSERT OR REPLACE INTO agent_iterations(id, session_id, iteration, story_id, story_title, status, started_at)
     VALUES('$iter_id', '$session_id', $iteration, '$story_id', '$story_title', 'running', datetime('now'));"

  # Increment story attempt count
  memory_query \
    "INSERT INTO story_state(story_id, attempt_count, last_session)
     VALUES('$story_id', 1, '$session_id')
     ON CONFLICT(story_id) DO UPDATE SET
       attempt_count = attempt_count + 1,
       last_session = '$session_id',
       last_updated = datetime('now');"

  memory_audit "$session_id" "$iteration" "$story_id" "ITERATION_START" "agent_iterations" "iter_id=$iter_id"
}

memory_end_iteration() {
  local session_id="$1"
  local iteration="$2"
  local story_id="$3"
  local status="$4"    # pass | fail | blocked
  local gate_result="$5"  # pass | fail | skipped
  local iter_id="${session_id}-${iteration}"

  memory_query \
    "UPDATE agent_iterations
     SET status='$status', gate_result='$gate_result', completed_at=datetime('now')
     WHERE id='$iter_id';"

  memory_audit "$session_id" "$iteration" "$story_id" "ITERATION_END" "agent_iterations" "status=$status gate=$gate_result"
}

# ── Messaging ──────────────────────────────────────────────
memory_post_message() {
  local session_id="$1"
  local iteration="$2"
  local story_id="$3"
  local msg_type="$4"   # DISCOVERY | BLOCKER | HANDOFF | WARNING | STATUS | DECISION
  local subject="${5//\'/\'\'}"
  local body="${6//\'/\'\'}"

  memory_query \
    "INSERT INTO agent_messages(from_session, from_iter, story_id, message_type, subject, body)
     VALUES('$session_id', $iteration, '$story_id', '$msg_type', '$subject', '$body');"
}

memory_mark_messages_read() {
  local session_id="$1"
  memory_query \
    "UPDATE agent_messages SET read_at=datetime('now') WHERE read_at IS NULL AND from_session != '$session_id';"
}

# ── Context Store ──────────────────────────────────────────
memory_set_context() {
  local key="${1//\'/\'\'}"
  local value="${2//\'/\'\'}"
  local scope="${3:-global}"
  local value_type="${4:-text}"
  local written_by="${5:-forge.sh}"

  memory_query \
    "INSERT INTO context_store(key, scope, value, value_type, written_by, updated_at)
     VALUES('$key', '$scope', '$value', '$value_type', '$written_by', datetime('now'))
     ON CONFLICT(key, scope) DO UPDATE SET
       value='$value', value_type='$value_type', written_by='$written_by', updated_at=datetime('now');"
}

memory_get_context() {
  local key="$1"
  local scope="${2:-global}"
  memory_query \
    "SELECT value FROM context_store WHERE key='$key' AND scope='$scope';"
}

# ── Audit ──────────────────────────────────────────────────
memory_audit() {
  local session_id="${1//\'/\'\'}"
  local iteration="${2:-NULL}"
  local story_id="${3//\'/\'\'}"
  local action="${4//\'/\'\'}"
  local entity="${5//\'/\'\'}"
  local detail="${6//\'/\'\'}"

  [[ "$iteration" == "" ]] && iteration="NULL"
  [[ "$story_id"  == "" ]] && story_id_sql="NULL" || story_id_sql="'$story_id'"
  [[ "$entity"    == "" ]] && entity_sql="NULL"   || entity_sql="'$entity'"
  [[ "$detail"    == "" ]] && detail_sql="NULL"   || detail_sql="'$detail'"

  memory_query \
    "INSERT INTO audit_log(session_id, iteration, story_id, action, entity, detail)
     VALUES('$session_id', $iteration, $story_id_sql, '$action', $entity_sql, $detail_sql);" 2>/dev/null || true
}

# ── Archive ────────────────────────────────────────────────
memory_archive() {
  local archive_path="$1"
  if [[ -f "$FORGE_DB" ]]; then
    sqlite3 "$FORGE_DB" ".backup '${archive_path}/forge-memory.db'"
    ok "Memory DB archived to: ${archive_path}/forge-memory.db"
  fi
}

# ── Startup Status Report (Markdown → Terminal) ────────────
memory_print_startup_report() {
  local session_id="$1"
  local project_name="$2"
  local branch_name="$3"

  # Gather stats
  local total_stories incomplete_stories complete_stories
  total_stories=$(jq '.userStories | length' prd.json 2>/dev/null || echo "0")
  incomplete_stories=$(jq '[.userStories[] | select(.passes == false)] | length' prd.json 2>/dev/null || echo "0")
  complete_stories=$(( total_stories - incomplete_stories ))

  local total_sessions prior_discoveries unread_messages
  total_sessions=$(memory_query "SELECT COUNT(*) FROM forge_sessions;" 2>/dev/null || echo "0")
  prior_discoveries=$(memory_query "SELECT COUNT(*) FROM discoveries;" 2>/dev/null || echo "0")
  unread_messages=$(memory_query "SELECT COUNT(*) FROM agent_messages WHERE read_at IS NULL;" 2>/dev/null || echo "0")

  local last_story_completed
  last_story_completed=$(memory_query \
    "SELECT story_id || ' — ' || completed_at FROM agent_iterations
     WHERE status='pass' ORDER BY completed_at DESC LIMIT 1;" 2>/dev/null || echo "none")

  # Build and print the markdown report
  echo ""
  echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}${CYAN}  FORGE MEMORY SYSTEM — STARTUP STATUS REPORT${RESET}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  printf "${BOLD}%-22s${RESET} %s\n" "  Project:"        "$project_name"
  printf "${BOLD}%-22s${RESET} %s\n" "  Branch:"         "$branch_name"
  printf "${BOLD}%-22s${RESET} %s\n" "  Session ID:"     "$session_id"
  printf "${BOLD}%-22s${RESET} %s\n" "  Memory DB:"      "$FORGE_DB"
  printf "${BOLD}%-22s${RESET} %s\n" "  Prior Sessions:" "$total_sessions"
  echo ""
  echo -e "${CYAN}  ── Story Pipeline ──────────────────────────────────────────${RESET}"
  printf "  ${GREEN}%-6s${RESET} complete   ${RED}%-6s${RESET} remaining   ${BOLD}%-6s${RESET} total\n" \
    "$complete_stories" "$incomplete_stories" "$total_stories"
  echo ""

  # Story table
  echo -e "  ${BOLD}ID         Pri  Status     Title${RESET}"
  echo -e "  ${CYAN}─────────────────────────────────────────────────────────${RESET}"
  jq -r '.userStories | sort_by(.priority) | .[] |
    "  \(.id)   \(.priority)    \(if .passes then "\u2713 pass  " else "○ open  " end)   \(.title)"' \
    prd.json 2>/dev/null | while IFS= read -r line; do
      if [[ "$line" == *"✓"* ]]; then
        echo -e "${GREEN}${line}${RESET}"
      else
        echo -e "${line}"
      fi
    done

  echo ""
  echo -e "${CYAN}  ── Agent Memory State ──────────────────────────────────────${RESET}"
  printf "${BOLD}  %-26s${RESET} %s\n" "Discoveries recorded:"    "$prior_discoveries"
  printf "${BOLD}  %-26s${RESET} %s\n" "Unread agent messages:"   "$unread_messages"
  printf "${BOLD}  %-26s${RESET} %s\n" "Last story completed:"    "$last_story_completed"
  echo ""

  # Show unread messages if any
  if [[ "$unread_messages" -gt 0 ]]; then
    echo -e "${YELLOW}  ── Unread Agent Messages ────────────────────────────────────${RESET}"
    memory_query \
      "SELECT '  [' || message_type || '] ' || subject || ' — ' || substr(body,1,60)
       FROM agent_messages WHERE read_at IS NULL ORDER BY created_at DESC LIMIT 5;" 2>/dev/null \
      | while IFS= read -r line; do echo -e "${YELLOW}${line}${RESET}"; done
    echo ""
  fi

  # Show last 3 discoveries
  if [[ "$prior_discoveries" -gt 0 ]]; then
    echo -e "${CYAN}  ── Recent Discoveries ───────────────────────────────────────${RESET}"
    memory_query \
      "SELECT '  [' || type || '] ' || title || ': ' || substr(detail,1,70)
       FROM discoveries ORDER BY created_at DESC LIMIT 3;" 2>/dev/null \
      | while IFS= read -r line; do echo -e "  ${line}"; done
    echo ""
  fi

  # Context store snapshot
  local ctx_count
  ctx_count=$(memory_query "SELECT COUNT(*) FROM context_store;" 2>/dev/null || echo "0")
  if [[ "$ctx_count" -gt 0 ]]; then
    echo -e "${CYAN}  ── Context Store (${ctx_count} entries) ─────────────────────────────${RESET}"
    memory_query \
      "SELECT '  ' || key || ' [' || scope || ']', substr(value,1,60) FROM context_store ORDER BY updated_at DESC LIMIT 5;" \
      2>/dev/null | while IFS= read -r line; do echo "  $line"; done
    echo ""
  fi

  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

  # Write report to file for agent consumption
  memory_export_startup_report "$session_id" "$project_name" "$branch_name" \
    "$total_sessions" "$prior_discoveries" "$unread_messages" "$last_story_completed"

  echo ""
}

# Export machine-readable version for agents
memory_export_startup_report() {
  local session_id="$1" project_name="$2" branch_name="$3"
  local total_sessions="$4" prior_discoveries="$5" unread_messages="$6" last_story="$7"

  cat > forge-startup-report.md <<MDEOF
# FORGE Memory System — Startup Report
**Generated:** $(date -u +"%Y-%m-%d %H:%M:%S UTC")
**Session ID:** ${session_id}
**Project:** ${project_name}
**Branch:** ${branch_name}
**Memory DB:** ${FORGE_DB}

---

## Agent Memory State

| Metric | Value |
|--------|-------|
| Prior FORGE sessions | ${total_sessions} |
| Discoveries in DB | ${prior_discoveries} |
| Unread agent messages | ${unread_messages} |
| Last story completed | ${last_story} |

---

## Unread Agent Messages

$(memory_query \
  "SELECT '### [' || message_type || '] ' || subject || char(10) || '**Story:** ' || coalesce(story_id,'—') || char(10) || '**From:** Session ' || from_session || ' Iteration ' || coalesce(from_iter,'?') || char(10) || body || char(10)
   FROM agent_messages WHERE read_at IS NULL ORDER BY created_at ASC;" 2>/dev/null \
  || echo "_No unread messages._")

---

## Recent Discoveries

$(memory_query \
  "SELECT '### [' || type || '] ' || title || char(10) || detail || char(10) || '**Story:** ' || story_id || ' | **Recorded:** ' || created_at || char(10)
   FROM discoveries ORDER BY created_at DESC LIMIT 10;" 2>/dev/null \
  || echo "_No discoveries recorded yet._")

---

## Context Store Snapshot

$(memory_query \
  "SELECT '**' || key || '** (' || scope || ', ' || value_type || ')  ' || char(10) || '> ' || value || char(10)
   FROM context_store ORDER BY updated_at DESC;" 2>/dev/null \
  || echo "_Context store is empty._")

---

## Story Attempt History

$(memory_query \
  "SELECT '- **' || story_id || '** — ' || attempt_count || ' attempt(s). Last: ' || coalesce(last_updated,'—') || coalesce('. Notes: ' || context_notes, '')
   FROM story_state ORDER BY last_updated DESC;" 2>/dev/null \
  || echo "_No story history yet._")

---
*This file is auto-generated by FORGE at session start. It is your primary context briefing. Read it before writing any code.*
MDEOF

  ok "Startup report written: forge-startup-report.md"
}

# ── DB Health Check ────────────────────────────────────────
memory_health_check() {
  local version
  version=$(memory_query "SELECT value FROM db_meta WHERE key='schema_version';" 2>/dev/null || echo "MISSING")

  if [[ "$version" != "$FORGE_DB_VERSION" ]]; then
    fail "Memory DB schema mismatch. Expected v${FORGE_DB_VERSION}, found '${version}'. Run: rm ${FORGE_DB} and restart."
  fi

  ok "Memory DB healthy (schema v${version}): ${FORGE_DB}"
}

# ── Sidecar Health Protocol (V2.2) ─────────────────────────
memory_check_sidecar() {
  local sidecar_name="$1"
  local max_age_seconds="${2:-30}"
  local last_seen

  last_seen=$(memory_query \
    "SELECT ts FROM audit_log 
     WHERE action='HEARTBEAT' AND entity='$sidecar_name' 
     ORDER BY ts DESC LIMIT 1;")

  if [[ -z "$last_seen" ]]; then
    return 1 # Never seen
  fi

  # Check if last_seen is within max_age
  local age
  age=$(memory_query "SELECT (strftime('%s', 'now') - strftime('%s', '$last_seen'));")
  
  if [[ "$age" -gt "$max_age_seconds" ]]; then
    return 2 # Stale
  fi

  return 0 # Healthy
}

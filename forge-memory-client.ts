/**
 * FORGE Memory Client — forge-memory-client.ts
 *
 * TypeScript interface to forge-memory.db for agent use.
 * Every Claude Code instance in the FORGE loop imports this.
 *
 * GOVERNANCE: See MEMORY_PROTOCOL.md
 * REQUIRED: Read on entry, write on exit.
 *
 * Dependencies: better-sqlite3 (install: npm i better-sqlite3 @types/better-sqlite3)
 */

import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync, appendFileSync, mkdirSync, writeFileSync } from 'fs';
import { createHash } from 'node:crypto';

// ── Types ─────────────────────────────────────────────────

export type MessageType = 'DISCOVERY' | 'BLOCKER' | 'HANDOFF' | 'WARNING' | 'STATUS' | 'DECISION';
export type DiscoveryType = 'PATTERN' | 'GOTCHA' | 'BLOCKER' | 'DECISION' | 'DEPENDENCY' | 'CONVENTION';
export type ContextValueType = 'text' | 'json' | 'path' | 'url';
export type IterationStatus = 'running' | 'pass' | 'fail' | 'blocked';
export type GateResult = 'pass' | 'fail' | 'skipped';

export interface AgentMessage {
  id: number;
  from_session: string;
  from_iter: number | null;
  story_id: string | null;
  message_type: MessageType;
  subject: string;
  body: string;
  created_at: string;
  read_at: string | null;
}

export interface Discovery {
  id: number;
  story_id: string;
  session_id: string;
  iteration: number;
  type: DiscoveryType | 'TRIGGER';
  title: string;
  detail: string;
  trigger_id?: string | null;
  payload_hash?: string | null;
  source?: string | null;
  created_at: string;
  exported_to_agents_md: number;
}

export interface ContextEntry {
  key: string;
  scope: string;
  value: string;
  value_type: ContextValueType;
  written_by: string;
  updated_at: string;
}

export interface StoryState {
  story_id: string;
  attempt_count: number;
  last_error: string | null;
  blockers: string[] | null;
  context_notes: string | null;
  last_session: string | null;
  last_updated: string;
}

export interface EntryContext {
  messages: AgentMessage[];
  storyState: StoryState | null;
  contextStore: ContextEntry[];
  startupReport: string;
}

export interface RefinementReport {
  sessionId: string;
  timestamp: string;
  shs: {
    score: number;
    factors: {
      successRate: number;
      efficiencyFactor: number;
      budgetFactor: number;
    };
    mode: 'EVOLVE' | 'REMEDIATION' | 'AUDIT' | 'INSUFFICIENT_SCOPE';
  };
  analytics: {
    totalStories: number;
    passedStories: number;
    totalIterations: number;
    avgIterationsPerStory: number;
    totalTokens: number;
  };
  manifest?: {
    staged_at: string;
    expires_at: string;
    source_checksum: string;
    status: 'pending' | 'applied' | 'expired';
  };
}

interface StoryStateRow {
  story_id: string;
  attempt_count: number;
  last_error: string | null;
  blockers: string | null;
  context_notes: string | null;
  last_session: string | null;
  last_updated: string;
}

// ── ForgeMemory Class ─────────────────────────────────────

export class ForgeMemory {
  private db: DatabaseSync;
  private sessionId: string;
  private iteration: number;
  private storyId: string;

  constructor(
    dbPath: string = 'forge-memory.db',
    sessionId: string,
    iteration: number,
    storyId: string
  ) {
    // 2.1 Native Portability Check
    const [major, minor] = process.versions.node.split('.').map(Number);
    if (major < 22 || (major === 22 && minor < 5)) {
      throw new Error(
        `[FORGE ERROR] Forge v2.0 requires Node.js 22.5.0+ for native SQLite support.\n` +
        `Current version: ${process.version}\n` +
        `Please upgrade Node.js to meet the portability mandate.`
      );
    }

    if (!existsSync(dbPath)) {
      throw new Error(
        `[FORGE MEMORY] DB not found at: ${dbPath}\n` +
        `forge.sh must run before any agent. The DB is initialized at startup.\n` +
        `MEMORY_PROTOCOL.md Rule 1 violation.`
      );
    }

    this.db = new DatabaseSync(dbPath);
    this.db.exec('PRAGMA journal_mode = WAL');
    this.db.exec('PRAGMA foreign_keys = ON');

    this.sessionId = sessionId;
    this.iteration = iteration;
    this.storyId = storyId;
  }

  /**
   * Function 0 Entry Gate — Read state before action.
   */
  async entry(): Promise<EntryContext> {
    console.log(`\n[FORGE MEMORY] ── Function 0 Entry Gate ─────────────────────`);
    
    this.markMessagesRead();
    
    const context: EntryContext = {
      messages: this.getUnreadMessages(),
      storyState: this.getStoryState(this.storyId),
      contextStore: this.getContextForStory(this.storyId),
      startupReport: this.readStartupReport(),
    };

    console.log(`[FORGE MEMORY] ✓ Session context loaded (${context.messages.length} messages)`);
    return context;
  }

  /**
   * Mandatory Exit Protocol — Persist state after action.
   */
  async exit(opts: {
    status: IterationStatus;
    gateResult: GateResult;
    summary: string;
    lastError?: string;
    blockers?: string[];
    discoveries?: Array<{ type: DiscoveryType | 'TRIGGER'; title: string; detail: string }>;
    contextEntries?: Array<{ key: string; value: string; scope: string }>;
  }): Promise<void> {
    console.log(`\n[FORGE MEMORY] ── Exit Protocol ──────────────────────────────`);
    const { status, gateResult, summary, lastError, blockers, discoveries, contextEntries } = opts;

    // 1. Post final status message
    this.postMessage(
      status === 'pass' ? 'STATUS' : 'BLOCKER',
      `[${this.storyId}] iter ${this.iteration} — ${status.toUpperCase()}`,
      summary
    );

    // 2. Record discoveries
    if (discoveries) {
      for (const d of discoveries) {
        this.recordDiscovery(d.type, d.title, d.detail);
      }
      console.log(`[FORGE MEMORY] ✓ ${discoveries.length} discoveries recorded`);
    }

    // 3. Persist context keys
    if (contextEntries) {
      for (const c of contextEntries) {
        this.setContext(c.key, c.value, c.scope);
      }
      console.log(`[FORGE MEMORY] ✓ ${contextEntries.length} context entries saved`);
    }

    // 4. Update story state
    this.updateStoryState({
      contextNotes: summary, // Use summary as base notes
      lastError: status === 'pass' ? null : (lastError ?? null),
      blockers: status === 'pass' ? [] : (blockers ?? null),
    });
    console.log('[FORGE MEMORY] ✓ Story state updated');

    // 5. Context Compression (V2-002)
    this.summarize(status, summary);

    // 6. Audit exit
    this.audit('AGENT_EXIT', 'agent_iterations', `status=${status} gate=${gateResult}`);

    console.log('[FORGE MEMORY] ── Exit Protocol Complete ───────────────────────\n');
  }

  /**
   * Summarize current iteration findings to progress.txt and AGENTS.md
   */
  private summarize(status: IterationStatus, summary: string): void {
    const timestamp = new Date().toISOString().replace('T', ' ').split('.')[0];
    const logEntry = `\n[${timestamp}] ${this.storyId} (iter ${this.iteration}): ${status.toUpperCase()} — ${summary}\n`;
    
    // Update progress.txt
    appendFileSync('progress.txt', logEntry);
    console.log('[FORGE MEMORY] ✓ progress.txt updated');

    // Update AGENTS.md with structured discoveries
    const discoveriesMd = this.compileAgentsMd();
    if (discoveriesMd) {
      appendFileSync('AGENTS.md', discoveriesMd);
      console.log('[FORGE MEMORY] ✓ AGENTS.md updated with new discoveries');
    }
  }

  // ── READS ────────────────────────────────────────────────

  readStartupReport(reportPath: string = 'forge-startup-report.md'): string {
    if (!existsSync(reportPath)) {
      return '(No startup report found — forge.sh may not have run cleanly)';
    }
    return readFileSync(reportPath, 'utf-8');
  }

  getUnreadMessages(): AgentMessage[] {
    return this.db.prepare(
      `SELECT * FROM agent_messages WHERE read_at IS NULL ORDER BY created_at ASC`
    ).all() as unknown as AgentMessage[];
  }

  getStoryState(storyId: string): StoryState | null {
    const row = this.db.prepare(
      `SELECT *, json(blockers) as blockers FROM story_state WHERE story_id = ?`
    ).get(storyId) as unknown as StoryStateRow | undefined;

    if (!row) return null;
    return {
      ...row,
      blockers: row.blockers ? JSON.parse(row.blockers) : null,
    };
  }

  getContextForStory(storyId: string): ContextEntry[] {
    return this.db.prepare(
      `SELECT * FROM context_store
       WHERE scope = 'global' OR scope = ?
       ORDER BY updated_at DESC`
    ).all(`story:${storyId}`) as unknown as ContextEntry[];
  }

  getContext(key: string, scope: string = 'global'): string | null {
    const row = this.db.prepare(
      `SELECT value FROM context_store WHERE key = ? AND scope = ?`
    ).get(key, scope) as { value: string } | undefined;
    return row?.value ?? null;
  }

  getDiscoveriesByStory(storyId: string): Discovery[] {
    return this.db.prepare(
      `SELECT * FROM discoveries WHERE story_id = ? ORDER BY created_at DESC`
    ).all(storyId) as unknown as Discovery[];
  }

  getAllDiscoveries(limit: number = 20): Discovery[] {
    return this.db.prepare(
      `SELECT * FROM discoveries ORDER BY created_at DESC LIMIT ?`
    ).all(limit) as unknown as Discovery[];
  }

  // ── WRITES ───────────────────────────────────────────────

  markMessagesRead(): void {
    this.db.prepare(
      `UPDATE agent_messages SET read_at = datetime('now')
       WHERE read_at IS NULL AND from_session != ?`
    ).run(this.sessionId);
  }

  postMessage(
    type: MessageType,
    subject: string,
    body: string,
    storyId?: string
  ): void {
    this.db.prepare(
      `INSERT INTO agent_messages(from_session, from_iter, story_id, message_type, subject, body)
       VALUES(?, ?, ?, ?, ?, ?)`
    ).run(
      this.sessionId,
      this.iteration,
      storyId ?? this.storyId,
      type,
      subject,
      body
    );
  }

  recordDiscovery(
    type: DiscoveryType | 'TRIGGER',
    title: string,
    detail: string,
    opts: { triggerId?: string; payloadHash?: string; source?: string } = {}
  ): void {
    this.db.prepare(
      `INSERT INTO discoveries(story_id, session_id, iteration, type, title, detail, trigger_id, payload_hash, source)
       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).run(
      this.storyId,
      this.sessionId,
      this.iteration,
      type,
      title,
      detail,
      opts.triggerId ?? null,
      opts.payloadHash ?? null,
      opts.source ?? null
    );
  }

  /**
   * Check for duplicate triggers (V2.1)
   */
  isTriggerDuplicate(source: string, triggerId: string, payloadHash: string): boolean {
    const byId = this.db.prepare(
      `SELECT id FROM discoveries WHERE source = ? AND trigger_id = ?`
    ).get(source, triggerId) as { id: number } | undefined;
    if (byId) return true;

    const byHash = this.db.prepare(
      `SELECT id FROM discoveries 
       WHERE source = ? AND payload_hash = ? 
       AND created_at > datetime('now', '-24 hours')`
    ).get(source, payloadHash) as { id: number } | undefined;
    
    return !!byHash;
  }

  setContext(
    key: string,
    value: string,
    scope: string = 'global',
    valueType: ContextValueType = 'text'
  ): void {
    this.db.prepare(
      `INSERT INTO context_store(key, scope, value, value_type, written_by, updated_at)
       VALUES(?, ?, ?, ?, ?, datetime('now'))
       ON CONFLICT(key, scope) DO UPDATE SET
         value = excluded.value,
         value_type = excluded.value_type,
         written_by = excluded.written_by,
         updated_at = datetime('now')`
    ).run(key, scope, value, valueType, `${this.sessionId}-${this.iteration}`);
  }

  updateStoryState(opts: {
    contextNotes?: string | null;
    lastError?: string | null;
    blockers?: string[] | null;
  }): void {
    const { contextNotes, lastError, blockers } = opts;
    this.db.prepare(
      `INSERT INTO story_state(story_id, attempt_count, context_notes, last_error, blockers, last_session, last_updated)
       VALUES(?, 1, ?, ?, ?, ?, datetime('now'))
       ON CONFLICT(story_id) DO UPDATE SET
         attempt_count = story_state.attempt_count + 1,
         context_notes = COALESCE(excluded.context_notes, story_state.context_notes),
         last_error    = excluded.last_error,
         blockers      = excluded.blockers,
         last_session  = excluded.last_session,
         last_updated  = datetime('now')`
    ).run(
      this.storyId,
      contextNotes ?? null,
      lastError ?? null,
      blockers ? JSON.stringify(blockers) : null,
      this.sessionId
    );
  }

  // ── AUDIT ────────────────────────────────────────────────

  audit(action: string, entity?: string, detail?: string): void {
    this.db.prepare(
      `INSERT INTO audit_log(session_id, iteration, story_id, action, entity, detail)
       VALUES(?, ?, ?, ?, ?, ?)`
    ).run(
      this.sessionId,
      this.iteration,
      this.storyId,
      action,
      entity ?? null,
      detail ?? null
    );
  }

  /**
   * Sidecar Heartbeat (V2.2)
   * Record that a sidecar/agent is still alive and healthy.
   */
  heartbeat(sidecarName: string, detail?: string): void {
    this.audit('HEARTBEAT', sidecarName, detail);
  }

  // ── UTILITIES ────────────────────────────────────────────

  /**
   * Compile discoveries into AGENTS.md format.
   * Call after all stories pass to update institutional memory.
   */
  compileAgentsMd(): string {
    const discoveries = this.db.prepare(
      `SELECT * FROM discoveries WHERE exported_to_agents_md = 0 ORDER BY created_at ASC`
    ).all() as unknown as Discovery[];

    if (!discoveries.length) return '';

    const sections = discoveries.reduce<Record<string, Discovery[]>>((acc, d) => {
      acc[d.type] = acc[d.type] ?? [];
      acc[d.type].push(d);
      return acc;
    }, {});

    let md = `\n## Auto-compiled from FORGE Discoveries — ${new Date().toISOString().split('T')[0]}\n\n`;

    for (const [type, items] of Object.entries(sections)) {
      md += `### ${type}S\n`;
      for (const d of items) {
        md += `- **[${d.story_id}] ${d.title}**: ${d.detail}\n`;
      }
      md += '\n';
    }

    // Mark exported
    this.db.prepare(
      `UPDATE discoveries SET exported_to_agents_md = 1 WHERE exported_to_agents_md = 0`
    ).run();

    return md;
  }

  close(): void {
    this.db.close();
  }
}

// ── ForgeRefiner Class (V2.2 SIC) ──────────────────────────

export class ForgeRefiner {
  constructor(private db: DatabaseSync) {}

  private computeHash(content: string): string {
    return createHash('sha256').update(content).digest('hex');
  }

  /**
   * Run full performance analysis for a session.
   */
  analyzeSession(sessionId: string, prdFile: string = 'prd.json'): RefinementReport {
    // 1. Load PRD stats
    const prd = JSON.parse(readFileSync(prdFile, 'utf-8'));
    const totalStories = prd.userStories?.length || 0;
    const passedStories = prd.userStories?.filter((s: any) => s.passes).length || 0;

    // 2. Query Telemetry
    const iterations = this.db.prepare(
      `SELECT count(*) as count FROM agent_iterations WHERE session_id = ?`
    ).get(sessionId) as { count: number };
    
    const totalIterations = iterations.count;

    // 3. SHS Calculation (PRD v2.2 Section 3.2)
    const SR = totalStories > 0 ? passedStories / totalStories : 0;
    const EF = totalIterations > 0 ? Math.min(1.0, totalStories / totalIterations) : 0;
    const BF = 1.0; 

    const score = (SR * 50) + (EF * 30) + (BF * 20);

    let mode: RefinementReport['shs']['mode'] = 'AUDIT';
    if (totalStories === 0) mode = 'INSUFFICIENT_SCOPE';
    else if (score > 80) mode = 'EVOLVE';
    else if (score >= 40) mode = 'REMEDIATION';

    const stagedAt = new Date();
    const expiresAt = new Date(stagedAt.getTime() + 7 * 24 * 60 * 60 * 1000);
    
    let sourceChecksum = 'NONE';
    if (existsSync('FORGE_RULES.md')) {
      sourceChecksum = this.computeHash(readFileSync('FORGE_RULES.md', 'utf-8'));
    }

    return {
      sessionId,
      timestamp: stagedAt.toISOString(),
      shs: {
        score: Math.round(score),
        factors: { successRate: SR, efficiencyFactor: EF, budgetFactor: BF },
        mode
      },
      analytics: {
        totalStories,
        passedStories,
        totalIterations,
        avgIterationsPerStory: totalStories > 0 ? totalIterations / totalStories : 0,
        totalTokens: 0 
      },
      manifest: {
        staged_at: stagedAt.toISOString(),
        expires_at: expiresAt.toISOString(),
        source_checksum: sourceChecksum,
        status: 'pending'
      }
    };
  }

  /**
   * Stage proposed rule changes (V2-009)
   */
  stageRefinement(sessionId: string): void {
    const report = this.analyzeSession(sessionId);
    const stagedDir = `staged-rules/session_${sessionId}`;
    
    if (!existsSync(stagedDir)) mkdirSync(stagedDir, { recursive: true });

    writeFileSync(`${stagedDir}/manifest.json`, JSON.stringify(report, null, 2));

    const delta = `\n## Proposed Rule from Session ${sessionId}\n- [AUTO] Always verify sidecar heartbeats before execution.\n`;
    writeFileSync(`${stagedDir}/proposed_delta.md`, delta);

    console.log(`[SIC] Refinement staged: ${stagedDir}/manifest.json`);
  }

  /**
   * Commit staged rules (V2-009)
   */
  commitRules(sessionId: string): void {
    const stagedDir = `staged-rules/session_${sessionId}`;
    const manifestPath = `${stagedDir}/manifest.json`;
    const deltaPath = `${stagedDir}/proposed_delta.md`;

    if (!existsSync(manifestPath)) {
      throw new Error(`[SIC ERROR] INVALID_SELECTOR: No staged rules found for session ${sessionId}`);
    }

    const report = JSON.parse(readFileSync(manifestPath, 'utf-8')) as RefinementReport;
    const manifest = report.manifest!;

    if (new Date() > new Date(manifest.expires_at)) {
      throw new Error(`[SIC ERROR] EXPIRED_MANIFEST: Delta for session ${sessionId} has expired.`);
    }

    if (manifest.status === 'applied') {
      throw new Error(`[SIC ERROR] REPLAY_ATTEMPT: Delta for session ${sessionId} was already applied.`);
    }

    const currentRules = readFileSync('FORGE_RULES.md', 'utf-8');
    const currentHash = this.computeHash(currentRules);
    if (currentHash !== manifest.source_checksum) {
      throw new Error(`[SIC ERROR] CHECKSUM_MISMATCH: FORGE_RULES.md has changed. Delta is stale.`);
    }

    const delta = readFileSync(deltaPath, 'utf-8');
    appendFileSync('FORGE_RULES.md', delta);

    manifest.status = 'applied';
    writeFileSync(manifestPath, JSON.stringify(report, null, 2));

    console.log(`[SIC] SUCCESS: Staged rules from session ${sessionId} applied to FORGE_RULES.md`);
  }

  /**
   * Generate a Post-Mortem Markdown report for GitHub (V2.4)
   */
  generatePostMortem(sessionId: string): string {
    const report = this.analyzeSession(sessionId);
    const { score, mode } = report.shs;
    
    // 1. Fetch Blockers
    const blockers = this.db.prepare(
      `SELECT subject, body, created_at FROM agent_messages 
       WHERE session_id = ? AND message_type = 'BLOCKER' 
       ORDER BY created_at DESC LIMIT 3`
    ).all(sessionId) as unknown as AgentMessage[];

    // 2. Fetch Handoffs
    const handoffs = this.db.prepare(
      `SELECT subject, body FROM agent_messages 
       WHERE session_id = ? AND message_type = 'HANDOFF' 
       ORDER BY created_at DESC LIMIT 5`
    ).all(sessionId) as unknown as AgentMessage[];

    let md = `# [FORGE FAILURE] Terminal State Reached — Session ${sessionId}\n\n`;
    md += `## 📊 Session Health Summary\n`;
    md += `- **Health Score:** ${score}/100\n`;
    md += `- **Operation Mode:** ${mode}\n`;
    md += `- **Stories:** ${report.analytics.passedStories}/${report.analytics.totalStories} passed\n`;
    md += `- **Iterations:** ${report.analytics.totalIterations}\n\n`;

    md += `## 🛑 Critical Blockers\n`;
    if (blockers.length > 0) {
      blockers.forEach(b => {
        md += `### ${b.subject} (${b.created_at})\n> ${b.body}\n\n`;
      });
    } else {
      md += `_No explicit blockers recorded in SQLite._\n\n`;
    }

    md += `## 🤝 Multi-Agent Handoffs\n`;
    if (handoffs.length > 0) {
      md += `<details>\n<summary>Click to view agent delegation logs</summary>\n\n`;
      handoffs.forEach(h => {
        md += `**${h.subject}**\n\`\`\`\n${h.body}\n\`\`\`\n\n`;
      });
      md += `</details>\n\n`;
    } else {
      md += `_No model-to-model handoffs occurred._\n\n`;
    }

    md += `--- \n*Generated autonomously by ForgeMP v2.4 Refiner*`;
    return md;
  }
}

// ── Standalone CLI helper ─────────────────────────────────
// Usage: npx ts-node forge-memory-client.ts <command> [args]

if (require.main === module) {
  const [,, cmd, ...args] = process.argv;
  const db = new DatabaseSync('forge-memory.db');

  switch (cmd) {
    case 'messages':
      console.log('Unread messages:');
      console.table(
        db.prepare(`SELECT id, message_type, subject, from_session FROM agent_messages WHERE read_at IS NULL`).all()
      );
      break;
    case 'discoveries':
      console.log('Recent discoveries:');
      console.table(
        db.prepare(`SELECT type, title, story_id, created_at FROM discoveries ORDER BY created_at DESC LIMIT 20`).all()
      );
      break;
    case 'context':
      console.log('Context store:');
      console.table(
        db.prepare(`SELECT key, scope, value, written_by FROM context_store ORDER BY updated_at DESC`).all()
      );
      break;
    case 'stories':
      console.log('Story state:');
      console.table(
        db.prepare(`SELECT story_id, attempt_count, last_error, last_updated FROM story_state`).all()
      );
      break;
    case 'audit': {
      const storyFilter = args[0];
      const rows = storyFilter
        ? db.prepare(`SELECT action, entity, detail, ts FROM audit_log WHERE story_id=? ORDER BY ts`).all(storyFilter)
        : db.prepare(`SELECT session_id, story_id, action, ts FROM audit_log ORDER BY ts DESC LIMIT 30`).all();
      console.table(rows);
      break;
    }
    case 'refine': {
      const sessionId = args[0];
      if (!sessionId) {
        console.error('Usage: refine <session-id>');
        process.exit(1);
      }
      const refiner = new ForgeRefiner(db);
      const report = refiner.analyzeSession(sessionId);
      console.log(JSON.stringify(report, null, 2));
      break;
    }
    case 'stage': {
      const sessionId = args[0];
      if (!sessionId) {
        console.error('Usage: stage <session-id>');
        process.exit(1);
      }
      const refiner = new ForgeRefiner(db);
      refiner.stageRefinement(sessionId);
      break;
    }
    case 'commit-rules': {
      const sessionId = args[0];
      if (!sessionId) {
        console.error('Usage: commit-rules <session-id>');
        process.exit(1);
      }
      const refiner = new ForgeRefiner(db);
      refiner.commitRules(sessionId);
      break;
    }
    case 'post-mortem': {
      const sessionId = args[0];
      if (!sessionId) {
        console.error('Usage: post-mortem <session-id>');
        process.exit(1);
      }
      const refiner = new ForgeRefiner(db);
      console.log(refiner.generatePostMortem(sessionId));
      break;
    }
    default:
      console.log('forge-memory-client CLI\nCommands: messages | discoveries | context | stories | audit [story-id] | refine [session-id] | stage [session-id] | commit-rules [session-id] | post-mortem [session-id]');
  }

  db.close();
}

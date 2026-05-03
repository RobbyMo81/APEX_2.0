// Co-authored by FORGE (Session: agent-0-tagteam-adhoc)
//! VSH-006: Heartbeat & Event Bus
//!
//! Continuous environment monitoring with delta classification
//! (routine/notable/urgent) and push-based event delivery to brain
//! via authenticated HTTP POST webhook.
//!
//! Key behaviors:
//! - Configurable pulse interval (default 60s; env VASHION_HEARTBEAT_INTERVAL).
//! - Snapshots: processes, containers, disk, memory, network, cpu.
//! - Delta classification: static thresholds + z-score anomaly detection.
//! - Z-score activates after 30 samples; cold-start static thresholds apply before.
//! - 5-minute dedup cooldown for z-score events; static threshold breaches bypass it.
//! - 24h pulse history with automatic pruning.
//! - Webhook delivery to brain /events with Bearer auth, 3-retry exponential backoff.
//! - Urgent events never dropped: written to local urgent queue on webhook failure.
//!
//! Safety gates (non-negotiable):
//! - Urgent events written to urgent queue if webhook fails after 3 retries.
//! - Webhook failures written to audit log.
//! - Static threshold breaches bypass dedup cooldown.
//! - Cooldown applies only to z-score/anomaly events.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet, VecDeque},
    fmt, fs,
    io::Write as IoWrite,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use sysinfo::{Disks, Networks, System};
use uuid::Uuid;

// ─── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_INTERVAL_SECS: u64 = 60;
const HISTORY_RETENTION_SECS: i64 = 86_400; // 24 h

/// Static urgent threshold: disk usage above this percentage is always urgent.
const DISK_URGENT_PCT: f64 = 85.0;
/// Static urgent threshold: free memory below this is always urgent.
const MEM_URGENT_MB: u64 = 512;
/// Mandatory service down threshold: urgent after this many consecutive missed pulses.
const SERVICE_DOWN_PULSE_THRESHOLD: u32 = 2;

/// Cold-start CPU threshold (applies before z-score has 30 samples).
const COLD_CPU_URGENT_PCT: f32 = 90.0;
/// Cold-start memory threshold (applies before z-score has 30 samples).
const COLD_MEM_URGENT_MB: u64 = 256;

/// Minimum samples before z-score anomaly detection activates.
const ZSCORE_MIN_SAMPLES: usize = 30;
/// Z-score threshold for anomaly classification (2σ).
const ZSCORE_THRESHOLD: f64 = 2.0;

/// Dedup cooldown window for z-score events (seconds). Does NOT apply to static thresholds.
const DEDUP_COOLDOWN_SECS: u64 = 300; // 5 min

/// Maximum webhook delivery attempts per event.
const WEBHOOK_MAX_RETRIES: u32 = 3;
/// Base backoff for webhook retry (doubles each attempt).
const WEBHOOK_BASE_BACKOFF_MS: u64 = 1_000;

// ─── Error type ───────────────────────────────────────────────────────────────

#[derive(Debug)]
pub enum HeartbeatError {
    Io(String),
    Serialize(String),
}

impl fmt::Display for HeartbeatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HeartbeatError::Io(s) => write!(f, "heartbeat I/O error: {s}"),
            HeartbeatError::Serialize(s) => write!(f, "heartbeat serialize error: {s}"),
        }
    }
}

// ─── Snapshot types ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessSnapshot {
    pub count: usize,
    /// For each required service name, whether it was found in the process list.
    pub required_services_up: HashMap<String, bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContainerSnapshot {
    /// Names of running containers.
    pub running: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiskSnapshot {
    /// Root filesystem usage percentage (0–100).
    pub used_pct: f64,
    /// Root filesystem free bytes.
    pub free_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemorySnapshot {
    pub total_mb: u64,
    pub free_mb: u64,
    pub used_pct: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetworkSnapshot {
    /// Cumulative bytes received across all interfaces.
    pub bytes_recv: u64,
    /// Cumulative bytes sent across all interfaces.
    pub bytes_sent: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CpuSnapshot {
    /// Global CPU usage percentage (0–100).
    pub usage_pct: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemSnapshot {
    pub timestamp: DateTime<Utc>,
    pub processes: ProcessSnapshot,
    pub containers: ContainerSnapshot,
    pub disk: DiskSnapshot,
    pub memory: MemorySnapshot,
    pub network: NetworkSnapshot,
    pub cpu: CpuSnapshot,
}

// ─── Delta classification ─────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DeltaClass {
    Routine,
    Notable,
    Urgent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceDelta {
    pub resource: String,
    pub detail: String,
    pub class: DeltaClass,
    /// When true: this delta fires regardless of dedup cooldown (static threshold breach).
    pub bypass_cooldown: bool,
}

// ─── Event type ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HeartbeatEvent {
    pub event_id: String,
    /// W3C trace context traceparent header value.
    pub traceparent: String,
    pub class: DeltaClass,
    pub resource: String,
    pub detail: String,
    pub timestamp: DateTime<Utc>,
}

impl HeartbeatEvent {
    fn new(class: DeltaClass, resource: String, detail: String) -> Self {
        HeartbeatEvent {
            event_id: Uuid::new_v4().to_string(),
            traceparent: generate_traceparent(),
            class,
            resource,
            detail,
            timestamp: Utc::now(),
        }
    }
}

/// Generate a W3C trace context traceparent: `00-{32hex}-{16hex}-01`
fn generate_traceparent() -> String {
    use rand::RngCore;
    let mut rng = rand::thread_rng();
    let mut trace = [0u8; 16];
    let mut span = [0u8; 8];
    rng.fill_bytes(&mut trace);
    rng.fill_bytes(&mut span);
    format!("00-{}-{}-01", hex::encode(trace), hex::encode(span))
}

// ─── Pulse history ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PulseHistoryEntry {
    pub timestamp: DateTime<Utc>,
    pub deltas: Vec<ResourceDelta>,
    pub overall_class: DeltaClass,
}

// ─── Z-score anomaly model ────────────────────────────────────────────────────

/// Sliding-window z-score anomaly detector.
/// Not active until `ZSCORE_MIN_SAMPLES` (30) samples have been collected;
/// before that, callers must fall back to cold-start static thresholds.
#[derive(Debug, Clone, Default)]
pub struct ZScoreModel {
    samples: VecDeque<f64>,
}

impl ZScoreModel {
    pub fn new() -> Self {
        ZScoreModel {
            samples: VecDeque::with_capacity(ZSCORE_MIN_SAMPLES * 2),
        }
    }

    pub fn push(&mut self, value: f64) {
        self.samples.push_back(value);
        // Bound the window at 2× minimum to avoid unbounded memory growth.
        while self.samples.len() > ZSCORE_MIN_SAMPLES * 2 {
            self.samples.pop_front();
        }
    }

    pub fn has_enough_samples(&self) -> bool {
        self.samples.len() >= ZSCORE_MIN_SAMPLES
    }

    /// Returns z-score of `value` against the current sample window.
    /// Returns `None` if fewer than `ZSCORE_MIN_SAMPLES` samples exist.
    pub fn z_score(&self, value: f64) -> Option<f64> {
        if !self.has_enough_samples() {
            return None;
        }
        let n = self.samples.len() as f64;
        let mean = self.samples.iter().sum::<f64>() / n;
        let variance = self.samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n;
        let stddev = variance.sqrt();
        // When all samples are identical (stddev≈0), use 1% of |mean| as a
        // minimum stddev floor so large absolute deviations are still detected.
        let effective_stddev = if stddev < f64::EPSILON {
            (mean.abs() * 0.01).max(1.0)
        } else {
            stddev
        };
        Some((value - mean).abs() / effective_stddev)
    }

    pub fn is_anomalous(&self, value: f64) -> bool {
        self.z_score(value)
            .map(|z| z > ZSCORE_THRESHOLD)
            .unwrap_or(false)
    }
}

// ─── Escalation state machine ─────────────────────────────────────────────────

/// Per-resource escalation state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EscalationState {
    Normal,
    /// One consecutive breach — escalating.
    Warning,
    /// Two or more consecutive breaches — escalated to urgent.
    Urgent,
    /// Three or more consecutive breaches — sustained critical state.
    SustainedUrgent,
}

/// Tracks consecutive breach counts per named resource.
pub struct EscalationTracker {
    // resource → (current state, consecutive breach count)
    states: HashMap<String, (EscalationState, u32)>,
}

impl EscalationTracker {
    pub fn new() -> Self {
        EscalationTracker {
            states: HashMap::new(),
        }
    }

    /// Record a breach for `resource`. Returns the new state after this breach.
    pub fn record_breach(&mut self, resource: &str) -> EscalationState {
        let entry = self
            .states
            .entry(resource.to_string())
            .or_insert((EscalationState::Normal, 0));
        entry.1 += 1;
        entry.0 = match entry.1 {
            1 => EscalationState::Warning,
            2 => EscalationState::Urgent,
            _ => EscalationState::SustainedUrgent,
        };
        entry.0.clone()
    }

    /// Record that `resource` is healthy. Resets count to zero.
    pub fn record_clear(&mut self, resource: &str) {
        self.states
            .insert(resource.to_string(), (EscalationState::Normal, 0));
    }

    pub fn state(&self, resource: &str) -> &EscalationState {
        self.states
            .get(resource)
            .map(|(s, _)| s)
            .unwrap_or(&EscalationState::Normal)
    }

    pub fn breach_count(&self, resource: &str) -> u32 {
        self.states.get(resource).map(|(_, c)| *c).unwrap_or(0)
    }
}

impl Default for EscalationTracker {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Dedup cooldown ───────────────────────────────────────────────────────────

/// 5-minute per-event dedup cooldown. Applied only to z-score/anomaly events.
/// Static threshold breaches always bypass this (bypass_cooldown=true on the delta).
struct DedupCooldown {
    last_fired: HashMap<String, Instant>,
    cooldown: Duration,
}

impl DedupCooldown {
    fn new() -> Self {
        DedupCooldown {
            last_fired: HashMap::new(),
            cooldown: Duration::from_secs(DEDUP_COOLDOWN_SECS),
        }
    }

    /// Returns true if `key` is within the cooldown window (suppress this event).
    fn is_suppressed(&self, key: &str) -> bool {
        self.last_fired
            .get(key)
            .map(|t| t.elapsed() < self.cooldown)
            .unwrap_or(false)
    }

    fn mark_fired(&mut self, key: &str) {
        self.last_fired.insert(key.to_string(), Instant::now());
    }
}

// ─── Audit log ────────────────────────────────────────────────────────────────

struct HeartbeatAuditLog {
    path: PathBuf,
}

impl HeartbeatAuditLog {
    fn new(path: PathBuf) -> Self {
        HeartbeatAuditLog { path }
    }

    fn write<T: Serialize>(&self, entry: &T) {
        let Ok(line) = serde_json::to_string(entry) else {
            return;
        };
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(mut file) = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            let _ = writeln!(file, "{line}");
        }
    }
}

// ─── HeartbeatEngine ──────────────────────────────────────────────────────────

/// Main heartbeat engine. Call `.run()` to start the pulse loop.
///
/// Holds a persistent `sysinfo::System` so CPU usage is computed as a delta
/// between successive `refresh_all()` calls (first pulse will show ~0% CPU).
pub struct HeartbeatEngine {
    pub interval: Duration,
    brain_url: String,
    auth_token: String,
    required_services: Vec<String>,

    // sysinfo instance — kept alive so CPU delta is computed correctly.
    system: System,

    // Runtime state
    prev_snapshot: Option<SystemSnapshot>,
    pulse_history: VecDeque<PulseHistoryEntry>,
    escalation: EscalationTracker,
    z_models: HashMap<String, ZScoreModel>,
    dedup: DedupCooldown,

    // Safety: urgent events are never dropped.
    urgent_queue: Vec<HeartbeatEvent>,
    urgent_queue_path: PathBuf,

    // HTTP client — reused across requests per reqwest best practice.
    http_client: reqwest::Client,

    audit: HeartbeatAuditLog,
}

impl HeartbeatEngine {
    pub fn new(brain_url: String, auth_token: String) -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        Self::new_with_home(brain_url, auth_token, Path::new(&home))
    }

    /// Explicit home path — avoids HOME env var races in tests (same pattern as FileEngine).
    pub fn new_with_home(brain_url: String, auth_token: String, home: &Path) -> Self {
        let interval_secs = std::env::var("VASHION_HEARTBEAT_INTERVAL")
            .ok()
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(DEFAULT_INTERVAL_SECS);
        let base = home.join(".vashion");
        HeartbeatEngine {
            interval: Duration::from_secs(interval_secs),
            brain_url,
            auth_token,
            required_services: vec![],
            system: System::new_all(),
            prev_snapshot: None,
            pulse_history: VecDeque::new(),
            escalation: EscalationTracker::new(),
            z_models: HashMap::new(),
            dedup: DedupCooldown::new(),
            urgent_queue: vec![],
            urgent_queue_path: base.join("heartbeat/urgent-queue.jsonl"),
            http_client: reqwest::Client::new(),
            audit: HeartbeatAuditLog::new(base.join("audit/heartbeat.jsonl")),
        }
    }

    pub fn with_required_services(mut self, services: Vec<String>) -> Self {
        self.required_services = services;
        self
    }

    // ─── Pulse loop ───────────────────────────────────────────────────────────

    /// Run the pulse loop indefinitely. Uses a tokio interval so pulses do not
    /// drift — they fire at the configured wall-clock interval.
    pub async fn run(&mut self) {
        let mut interval = tokio::time::interval(self.interval);
        loop {
            interval.tick().await;
            self.pulse().await;
        }
    }

    /// Execute one heartbeat pulse: collect, classify, dispatch.
    pub async fn pulse(&mut self) {
        let snapshot = self.collect_snapshot();

        // Clone previous snapshot before mutating self, avoiding borrow conflicts.
        let prev = self.prev_snapshot.clone();
        let deltas = self.compute_deltas(&snapshot, prev.as_ref());
        self.prev_snapshot = Some(snapshot.clone());

        let overall_class = overall_class_of(&deltas);
        self.pulse_history.push_back(PulseHistoryEntry {
            timestamp: snapshot.timestamp,
            deltas: deltas.clone(),
            overall_class: overall_class.clone(),
        });
        self.prune_pulse_history();

        // Dispatch — urgent events fire immediately outside pulse schedule.
        let mut urgent: Vec<HeartbeatEvent> = vec![];
        let mut notable: Vec<HeartbeatEvent> = vec![];

        for delta in &deltas {
            let key = &delta.resource;
            match &delta.class {
                DeltaClass::Urgent => {
                    // Static threshold breaches (bypass_cooldown=true) always fire.
                    if delta.bypass_cooldown || !self.dedup.is_suppressed(key) {
                        urgent.push(HeartbeatEvent::new(
                            DeltaClass::Urgent,
                            delta.resource.clone(),
                            delta.detail.clone(),
                        ));
                        if !delta.bypass_cooldown {
                            self.dedup.mark_fired(key);
                        }
                    }
                }
                DeltaClass::Notable => {
                    if !self.dedup.is_suppressed(key) {
                        notable.push(HeartbeatEvent::new(
                            DeltaClass::Notable,
                            delta.resource.clone(),
                            delta.detail.clone(),
                        ));
                        self.dedup.mark_fired(key);
                    }
                }
                DeltaClass::Routine => {
                    self.audit.write(&serde_json::json!({
                        "level": "routine",
                        "resource": delta.resource,
                        "detail": delta.detail,
                        "ts": snapshot.timestamp,
                    }));
                }
            }
        }

        // Urgent fires immediately (bypass pulse schedule).
        for event in urgent {
            self.deliver_urgent(event).await;
        }
        for event in notable {
            self.deliver_notable(event).await;
        }
    }

    // ─── Snapshot collection ─────────────────────────────────────────────────

    fn collect_snapshot(&mut self) -> SystemSnapshot {
        // Refresh sysinfo state. CPU usage is a delta since last refresh.
        self.system.refresh_all();

        let total_mem_mb = self.system.total_memory() / 1_048_576;
        let free_mem_mb = self.system.available_memory() / 1_048_576;
        let used_pct_mem = if total_mem_mb > 0 {
            (1.0 - free_mem_mb as f64 / total_mem_mb as f64) * 100.0
        } else {
            0.0
        };
        let memory = MemorySnapshot {
            total_mb: total_mem_mb,
            free_mb: free_mem_mb,
            used_pct: used_pct_mem,
        };

        let cpu = CpuSnapshot {
            usage_pct: self.system.global_cpu_info().cpu_usage(),
        };

        // Check required services by scanning process names.
        let mut services_up: HashMap<String, bool> = HashMap::new();
        for svc in &self.required_services {
            let up = self
                .system
                .processes()
                .values()
                .any(|p| p.name() == svc.as_str());
            services_up.insert(svc.clone(), up);
        }
        let processes = ProcessSnapshot {
            count: self.system.processes().len(),
            required_services_up: services_up,
        };

        // Disk: look for the root mount point.
        let disks = Disks::new_with_refreshed_list();
        let (free_bytes, used_pct_disk) = disks
            .iter()
            .find(|d| d.mount_point() == Path::new("/"))
            .map(|d| {
                let total = d.total_space();
                let avail = d.available_space();
                let pct = if total > 0 {
                    (total - avail) as f64 / total as f64 * 100.0
                } else {
                    0.0
                };
                (avail, pct)
            })
            .unwrap_or((0, 0.0));
        let disk = DiskSnapshot {
            used_pct: used_pct_disk,
            free_bytes,
        };

        // Network: sum all interfaces.
        let networks = Networks::new_with_refreshed_list();
        let (bytes_recv, bytes_sent) = networks.iter().fold((0u64, 0u64), |(r, s), (_, data)| {
            (r + data.received(), s + data.transmitted())
        });
        let network = NetworkSnapshot {
            bytes_recv,
            bytes_sent,
        };

        // Containers: shell out to docker ps.
        let containers = collect_containers();

        SystemSnapshot {
            timestamp: Utc::now(),
            processes,
            containers,
            disk,
            memory,
            network,
            cpu,
        }
    }

    // ─── Delta classification ─────────────────────────────────────────────────

    /// Compute resource deltas from `snap` relative to `prev`.
    /// Mutates z_models and escalation state.
    pub fn compute_deltas(
        &mut self,
        snap: &SystemSnapshot,
        prev: Option<&SystemSnapshot>,
    ) -> Vec<ResourceDelta> {
        let mut deltas = vec![];

        // Push metric samples first, then classify (avoids double-borrow on z_models).
        self.z_models
            .entry("mem_free_mb".into())
            .or_default()
            .push(snap.memory.free_mb as f64);
        self.z_models
            .entry("disk_used_pct".into())
            .or_default()
            .push(snap.disk.used_pct);
        self.z_models
            .entry("cpu_pct".into())
            .or_default()
            .push(snap.cpu.usage_pct as f64);

        deltas.push(self.classify_memory(snap));
        deltas.push(self.classify_disk(snap));
        deltas.push(self.classify_cpu(snap));

        // Required service health checks.
        let svc_names: Vec<String> = snap
            .processes
            .required_services_up
            .keys()
            .cloned()
            .collect();
        for svc in svc_names {
            let up = snap.processes.required_services_up[&svc];
            let resource = format!("service:{svc}");
            if up {
                self.escalation.record_clear(&resource);
            } else {
                let state = self.escalation.record_breach(&resource);
                let count = self.escalation.breach_count(&resource);
                let class = if state == EscalationState::Urgent
                    || state == EscalationState::SustainedUrgent
                {
                    DeltaClass::Urgent
                } else {
                    DeltaClass::Notable
                };
                deltas.push(ResourceDelta {
                    resource: resource.clone(),
                    detail: format!(
                        "service '{svc}' not running (consecutive missed pulses: {count})"
                    ),
                    class,
                    // Service down is a static threshold: bypass cooldown.
                    bypass_cooldown: count >= SERVICE_DOWN_PULSE_THRESHOLD,
                });
            }
        }

        // Network: notable if receive delta is anomalous vs z-score baseline.
        if let Some(prev_snap) = prev {
            let recv_delta = snap
                .network
                .bytes_recv
                .saturating_sub(prev_snap.network.bytes_recv);
            let net_model = self.z_models.entry("net_recv".into()).or_default();
            net_model.push(recv_delta as f64);
            if net_model.is_anomalous(recv_delta as f64) {
                deltas.push(ResourceDelta {
                    resource: "net_recv".into(),
                    detail: format!("anomalous inbound network traffic: {recv_delta} bytes/pulse"),
                    class: DeltaClass::Notable,
                    bypass_cooldown: false,
                });
            }

            // Container changes: stopped containers are notable.
            let prev_set: HashSet<&str> = prev_snap
                .containers
                .running
                .iter()
                .map(|s| s.as_str())
                .collect();
            let cur_set: HashSet<&str> =
                snap.containers.running.iter().map(|s| s.as_str()).collect();
            for stopped in prev_set.difference(&cur_set) {
                deltas.push(ResourceDelta {
                    resource: format!("container:{stopped}"),
                    detail: format!("container '{stopped}' stopped"),
                    class: DeltaClass::Notable,
                    bypass_cooldown: false,
                });
            }
        }

        deltas
    }

    fn classify_memory(&mut self, snap: &SystemSnapshot) -> ResourceDelta {
        // Static: mem < 512 MB → always urgent, bypass cooldown.
        if snap.memory.free_mb < MEM_URGENT_MB {
            self.escalation.record_breach("memory");
            return ResourceDelta {
                resource: "memory".into(),
                detail: format!(
                    "free memory {} MB below {} MB static threshold",
                    snap.memory.free_mb, MEM_URGENT_MB
                ),
                class: DeltaClass::Urgent,
                bypass_cooldown: true,
            };
        }
        self.escalation.record_clear("memory");

        let model = self.z_models.get("mem_free_mb").unwrap();
        if !model.has_enough_samples() {
            // Cold-start: fall back to 256 MB threshold.
            if snap.memory.free_mb < COLD_MEM_URGENT_MB {
                return ResourceDelta {
                    resource: "memory".into(),
                    detail: format!(
                        "cold-start: free memory {} MB < {} MB",
                        snap.memory.free_mb, COLD_MEM_URGENT_MB
                    ),
                    class: DeltaClass::Urgent,
                    bypass_cooldown: false,
                };
            }
            return ResourceDelta {
                resource: "memory".into(),
                detail: format!(
                    "memory OK: {} MB free (warming up z-score)",
                    snap.memory.free_mb
                ),
                class: DeltaClass::Routine,
                bypass_cooldown: false,
            };
        }

        if model.is_anomalous(snap.memory.free_mb as f64) {
            return ResourceDelta {
                resource: "memory".into(),
                detail: format!("z-score anomaly: free memory {} MB", snap.memory.free_mb),
                class: DeltaClass::Notable,
                bypass_cooldown: false,
            };
        }

        ResourceDelta {
            resource: "memory".into(),
            detail: format!("memory OK: {} MB free", snap.memory.free_mb),
            class: DeltaClass::Routine,
            bypass_cooldown: false,
        }
    }

    fn classify_disk(&mut self, snap: &SystemSnapshot) -> ResourceDelta {
        // Static: disk > 85% → always urgent, bypass cooldown.
        if snap.disk.used_pct > DISK_URGENT_PCT {
            self.escalation.record_breach("disk");
            return ResourceDelta {
                resource: "disk".into(),
                detail: format!(
                    "disk usage {:.1}% exceeds {:.0}% static threshold",
                    snap.disk.used_pct, DISK_URGENT_PCT
                ),
                class: DeltaClass::Urgent,
                bypass_cooldown: true,
            };
        }
        self.escalation.record_clear("disk");

        let model = self.z_models.get("disk_used_pct").unwrap();
        if model.has_enough_samples() && model.is_anomalous(snap.disk.used_pct) {
            return ResourceDelta {
                resource: "disk".into(),
                detail: format!("z-score anomaly: disk usage {:.1}%", snap.disk.used_pct),
                class: DeltaClass::Notable,
                bypass_cooldown: false,
            };
        }

        ResourceDelta {
            resource: "disk".into(),
            detail: format!("disk OK: {:.1}% used", snap.disk.used_pct),
            class: DeltaClass::Routine,
            bypass_cooldown: false,
        }
    }

    fn classify_cpu(&mut self, snap: &SystemSnapshot) -> ResourceDelta {
        let model = self.z_models.get("cpu_pct").unwrap();
        if !model.has_enough_samples() {
            // Cold-start: CPU > 90% → urgent.
            if snap.cpu.usage_pct > COLD_CPU_URGENT_PCT {
                return ResourceDelta {
                    resource: "cpu".into(),
                    detail: format!(
                        "cold-start: CPU {:.1}% > {:.0}%",
                        snap.cpu.usage_pct, COLD_CPU_URGENT_PCT
                    ),
                    class: DeltaClass::Urgent,
                    bypass_cooldown: false,
                };
            }
            return ResourceDelta {
                resource: "cpu".into(),
                detail: format!("cpu OK: {:.1}% (warming up z-score)", snap.cpu.usage_pct),
                class: DeltaClass::Routine,
                bypass_cooldown: false,
            };
        }

        if model.is_anomalous(snap.cpu.usage_pct as f64) {
            return ResourceDelta {
                resource: "cpu".into(),
                detail: format!("z-score anomaly: CPU {:.1}%", snap.cpu.usage_pct),
                class: DeltaClass::Notable,
                bypass_cooldown: false,
            };
        }

        ResourceDelta {
            resource: "cpu".into(),
            detail: format!("cpu OK: {:.1}%", snap.cpu.usage_pct),
            class: DeltaClass::Routine,
            bypass_cooldown: false,
        }
    }

    fn prune_pulse_history(&mut self) {
        let cutoff = Utc::now() - chrono::Duration::seconds(HISTORY_RETENTION_SECS);
        while let Some(front) = self.pulse_history.front() {
            if front.timestamp < cutoff {
                self.pulse_history.pop_front();
            } else {
                break;
            }
        }
    }

    // ─── Webhook delivery ─────────────────────────────────────────────────────

    /// Deliver an urgent event immediately. On failure: persist to local urgent queue.
    async fn deliver_urgent(&mut self, event: HeartbeatEvent) {
        match self.try_webhook_delivery(&event).await {
            Ok(()) => {}
            Err(e) => {
                self.audit.write(&serde_json::json!({
                    "level": "ERROR",
                    "action": "webhook_failure",
                    "event_id": event.event_id,
                    "resource": event.resource,
                    "error": e,
                    "ts": Utc::now(),
                }));
                // Safety gate: urgent events are never dropped.
                self.append_to_urgent_queue(&event);
            }
        }
    }

    /// Deliver a notable event. On failure: audit-log only (not queued urgently).
    async fn deliver_notable(&mut self, event: HeartbeatEvent) {
        if let Err(e) = self.try_webhook_delivery(&event).await {
            self.audit.write(&serde_json::json!({
                "level": "WARN",
                "action": "webhook_failure_notable",
                "event_id": event.event_id,
                "resource": event.resource,
                "error": e,
                "ts": Utc::now(),
            }));
        }
    }

    /// Attempt webhook delivery. Retries up to `WEBHOOK_MAX_RETRIES` times with
    /// exponential backoff (1 s, 2 s, 4 s). All failures are returned as Err.
    pub async fn try_webhook_delivery(&self, event: &HeartbeatEvent) -> Result<(), String> {
        let url = format!("{}/events", self.brain_url.trim_end_matches('/'));
        let mut last_err = String::new();

        for attempt in 0..WEBHOOK_MAX_RETRIES {
            if attempt > 0 {
                let backoff_ms = WEBHOOK_BASE_BACKOFF_MS * (1u64 << attempt);
                tokio::time::sleep(Duration::from_millis(backoff_ms)).await;
            }
            match self
                .http_client
                .post(&url)
                .header("Authorization", format!("Bearer {}", self.auth_token))
                .header("traceparent", &event.traceparent)
                .json(event)
                .send()
                .await
            {
                Ok(resp) if resp.status().is_success() => return Ok(()),
                Ok(resp) => last_err = format!("HTTP {}", resp.status()),
                Err(e) => last_err = e.to_string(),
            }
        }
        Err(format!(
            "webhook failed after {} attempts: {}",
            WEBHOOK_MAX_RETRIES, last_err
        ))
    }

    /// Persist event to the local urgent queue (disk + in-memory). Never returns error.
    fn append_to_urgent_queue(&mut self, event: &HeartbeatEvent) {
        self.urgent_queue.push(event.clone());
        if let Some(parent) = self.urgent_queue_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(line) = serde_json::to_string(event) {
            if let Ok(mut f) = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.urgent_queue_path)
            {
                let _ = writeln!(f, "{line}");
            }
        }
    }

    /// Attempt to deliver all queued urgent events. Events that fail remain queued.
    pub async fn flush_urgent_queue(&mut self) {
        let events = std::mem::take(&mut self.urgent_queue);
        let mut still_failed = vec![];
        for event in events {
            if self.try_webhook_delivery(&event).await.is_err() {
                still_failed.push(event);
            }
        }
        self.urgent_queue = still_failed;
    }

    // ─── Public accessors ─────────────────────────────────────────────────────

    pub fn pulse_history(&self) -> &VecDeque<PulseHistoryEntry> {
        &self.pulse_history
    }

    pub fn urgent_queue_len(&self) -> usize {
        self.urgent_queue.len()
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn overall_class_of(deltas: &[ResourceDelta]) -> DeltaClass {
    deltas
        .iter()
        .map(|d| &d.class)
        .fold(DeltaClass::Routine, |acc, c| match (&acc, c) {
            (_, DeltaClass::Urgent) | (DeltaClass::Urgent, _) => DeltaClass::Urgent,
            (_, DeltaClass::Notable) | (DeltaClass::Notable, _) => DeltaClass::Notable,
            _ => DeltaClass::Routine,
        })
}

fn collect_containers() -> ContainerSnapshot {
    use std::process::Command;
    let running = Command::new("docker")
        .args(["ps", "--format", "{{.Names}}"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter(|l| !l.trim().is_empty())
                .map(|l| l.to_string())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    ContainerSnapshot { running }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_snap(
        mem_free_mb: u64,
        disk_used_pct: f64,
        cpu_pct: f32,
        services: HashMap<String, bool>,
    ) -> SystemSnapshot {
        let total_mb = 4096u64;
        SystemSnapshot {
            timestamp: Utc::now(),
            processes: ProcessSnapshot {
                count: 5,
                required_services_up: services,
            },
            containers: ContainerSnapshot { running: vec![] },
            disk: DiskSnapshot {
                used_pct: disk_used_pct,
                free_bytes: 10_000_000,
            },
            memory: MemorySnapshot {
                total_mb,
                free_mb: mem_free_mb,
                used_pct: (1.0 - mem_free_mb as f64 / total_mb as f64) * 100.0,
            },
            network: NetworkSnapshot {
                bytes_recv: 0,
                bytes_sent: 0,
            },
            cpu: CpuSnapshot { usage_pct: cpu_pct },
        }
    }

    fn engine(tmp: &tempfile::TempDir) -> HeartbeatEngine {
        HeartbeatEngine::new_with_home(
            "http://127.0.0.1:19999".into(),
            "test-token".into(),
            tmp.path(),
        )
    }

    // ── ZScoreModel ───────────────────────────────────────────────────────────

    #[test]
    fn zscore_not_active_below_30_samples() {
        let mut model = ZScoreModel::new();
        for i in 0..29 {
            model.push(i as f64);
        }
        assert!(
            !model.has_enough_samples(),
            "should not activate with 29 samples"
        );
    }

    #[test]
    fn zscore_active_at_30_samples() {
        let mut model = ZScoreModel::new();
        for i in 0..30 {
            model.push(i as f64);
        }
        assert!(model.has_enough_samples());
    }

    #[test]
    fn zscore_detects_clear_anomaly() {
        let mut model = ZScoreModel::new();
        for _ in 0..30 {
            model.push(100.0);
        }
        assert!(
            model.is_anomalous(300.0),
            "300 vs mean=100 stddev≈0 should be anomalous"
        );
        assert!(
            !model.is_anomalous(100.5),
            "100.5 vs mean=100 should not be anomalous"
        );
    }

    #[test]
    fn zscore_zero_stddev_no_false_positive() {
        let mut model = ZScoreModel::new();
        for _ in 0..30 {
            model.push(42.0);
        }
        // stddev = 0 → z-score = 0 → not anomalous
        assert!(!model.is_anomalous(42.0));
    }

    #[test]
    fn zscore_z_score_returns_none_below_min_samples() {
        let model = ZScoreModel::new();
        assert_eq!(model.z_score(99.0), None);
    }

    // ── EscalationTracker ─────────────────────────────────────────────────────

    #[test]
    fn escalation_first_breach_is_warning() {
        let mut t = EscalationTracker::new();
        assert_eq!(t.record_breach("svc"), EscalationState::Warning);
    }

    #[test]
    fn escalation_second_breach_is_urgent() {
        let mut t = EscalationTracker::new();
        t.record_breach("svc");
        assert_eq!(t.record_breach("svc"), EscalationState::Urgent);
    }

    #[test]
    fn escalation_third_breach_is_sustained_urgent() {
        let mut t = EscalationTracker::new();
        t.record_breach("svc");
        t.record_breach("svc");
        assert_eq!(t.record_breach("svc"), EscalationState::SustainedUrgent);
    }

    #[test]
    fn escalation_clear_resets_to_normal() {
        let mut t = EscalationTracker::new();
        t.record_breach("svc");
        t.record_breach("svc");
        t.record_clear("svc");
        assert_eq!(t.state("svc"), &EscalationState::Normal);
        assert_eq!(t.breach_count("svc"), 0);
    }

    // ── Dedup cooldown ────────────────────────────────────────────────────────

    #[test]
    fn dedup_suppresses_within_window() {
        let mut d = DedupCooldown::new();
        d.mark_fired("disk");
        assert!(d.is_suppressed("disk"));
    }

    #[test]
    fn dedup_allows_after_window_expires() {
        let mut d = DedupCooldown {
            last_fired: HashMap::new(),
            cooldown: Duration::from_millis(1),
        };
        d.mark_fired("disk");
        std::thread::sleep(Duration::from_millis(10));
        assert!(!d.is_suppressed("disk"));
    }

    #[test]
    fn dedup_unknown_key_not_suppressed() {
        let d = DedupCooldown::new();
        assert!(!d.is_suppressed("never-fired"));
    }

    // ── Static threshold classifications ─────────────────────────────────────

    #[test]
    fn disk_over_85pct_is_urgent_with_bypass() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        let snap = make_snap(1024, 90.0, 5.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let disk = deltas.iter().find(|d| d.resource == "disk").unwrap();
        assert_eq!(disk.class, DeltaClass::Urgent);
        assert!(
            disk.bypass_cooldown,
            "static threshold must bypass cooldown"
        );
    }

    #[test]
    fn memory_under_512mb_is_urgent_with_bypass() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        let snap = make_snap(256, 50.0, 5.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let mem = deltas.iter().find(|d| d.resource == "memory").unwrap();
        assert_eq!(mem.class, DeltaClass::Urgent);
        assert!(mem.bypass_cooldown, "static threshold must bypass cooldown");
    }

    #[test]
    fn disk_under_threshold_is_routine_cold_start() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        let snap = make_snap(1024, 50.0, 5.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let disk = deltas.iter().find(|d| d.resource == "disk").unwrap();
        assert_eq!(disk.class, DeltaClass::Routine);
    }

    // ── Cold-start CPU threshold ──────────────────────────────────────────────

    #[test]
    fn cold_start_cpu_over_90pct_is_urgent() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        let snap = make_snap(1024, 50.0, 95.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let cpu = deltas.iter().find(|d| d.resource == "cpu").unwrap();
        assert_eq!(cpu.class, DeltaClass::Urgent);
    }

    #[test]
    fn cold_start_cpu_under_90pct_is_routine() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        let snap = make_snap(1024, 50.0, 30.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let cpu = deltas.iter().find(|d| d.resource == "cpu").unwrap();
        assert_eq!(cpu.class, DeltaClass::Routine);
    }

    // ── Mandatory service down escalation ────────────────────────────────────

    #[test]
    fn service_down_1_pulse_is_notable() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp).with_required_services(vec!["brain".into()]);
        let mut svc = HashMap::new();
        svc.insert("brain".into(), false);
        let snap = make_snap(1024, 50.0, 5.0, svc);
        let deltas = e.compute_deltas(&snap, None);
        let svc_d = deltas
            .iter()
            .find(|d| d.resource == "service:brain")
            .unwrap();
        assert_eq!(
            svc_d.class,
            DeltaClass::Notable,
            "first breach = warning = notable"
        );
    }

    #[test]
    fn service_down_2_pulses_is_urgent() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp).with_required_services(vec!["brain".into()]);
        let mut svc = HashMap::new();
        svc.insert("brain".into(), false);
        let snap = make_snap(1024, 50.0, 5.0, svc.clone());
        // First pulse → warning
        e.compute_deltas(&snap, None);
        // Second pulse → urgent
        let snap2 = make_snap(1024, 50.0, 5.0, svc);
        let deltas = e.compute_deltas(&snap2, None);
        let svc_d = deltas
            .iter()
            .find(|d| d.resource == "service:brain")
            .unwrap();
        assert_eq!(svc_d.class, DeltaClass::Urgent);
        assert!(
            svc_d.bypass_cooldown,
            "static threshold after 2 pulses must bypass cooldown"
        );
    }

    #[test]
    fn service_recovery_clears_escalation() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp).with_required_services(vec!["brain".into()]);
        let mut down = HashMap::new();
        down.insert("brain".into(), false);
        let mut up = HashMap::new();
        up.insert("brain".into(), true);

        e.compute_deltas(&make_snap(1024, 50.0, 5.0, down.clone()), None);
        e.compute_deltas(&make_snap(1024, 50.0, 5.0, down), None);
        assert_eq!(
            e.escalation.state("service:brain"),
            &EscalationState::Urgent
        );

        e.compute_deltas(&make_snap(1024, 50.0, 5.0, up), None);
        assert_eq!(
            e.escalation.state("service:brain"),
            &EscalationState::Normal
        );
    }

    // ── Static threshold bypasses cooldown ────────────────────────────────────

    #[test]
    fn static_threshold_fires_even_when_cooldown_active() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);
        // Mark disk as already fired (within cooldown window).
        e.dedup.mark_fired("disk");
        assert!(e.dedup.is_suppressed("disk"));

        let snap = make_snap(1024, 90.0, 5.0, HashMap::new());
        let deltas = e.compute_deltas(&snap, None);
        let disk = deltas.iter().find(|d| d.resource == "disk").unwrap();
        // bypass_cooldown=true means the pulse() dispatcher will fire it regardless.
        assert!(disk.bypass_cooldown);
    }

    // ── Pulse history pruning ─────────────────────────────────────────────────

    #[test]
    fn pulse_history_prunes_entries_older_than_24h() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);

        let old = Utc::now() - chrono::Duration::hours(25);
        e.pulse_history.push_back(PulseHistoryEntry {
            timestamp: old,
            deltas: vec![],
            overall_class: DeltaClass::Routine,
        });
        e.pulse_history.push_back(PulseHistoryEntry {
            timestamp: Utc::now(),
            deltas: vec![],
            overall_class: DeltaClass::Routine,
        });

        e.prune_pulse_history();
        assert_eq!(e.pulse_history.len(), 1);
        assert!(e.pulse_history[0].timestamp > old);
    }

    #[test]
    fn pulse_history_retains_entries_within_24h() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);

        for h in 0..5 {
            e.pulse_history.push_back(PulseHistoryEntry {
                timestamp: Utc::now() - chrono::Duration::hours(h),
                deltas: vec![],
                overall_class: DeltaClass::Routine,
            });
        }
        e.prune_pulse_history();
        assert_eq!(e.pulse_history.len(), 5);
    }

    // ── Urgent queue — never-drop guarantee ───────────────────────────────────

    #[tokio::test]
    async fn urgent_event_written_to_queue_on_delivery_failure() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp); // brain_url = localhost:19999 (nothing listening)

        let event = HeartbeatEvent::new(DeltaClass::Urgent, "disk".into(), "disk > 85%".into());
        e.deliver_urgent(event).await;
        assert_eq!(
            e.urgent_queue_len(),
            1,
            "urgent event must be queued on webhook failure"
        );
    }

    #[tokio::test]
    async fn urgent_queue_persisted_to_disk() {
        let tmp = tempfile::tempdir().unwrap();
        let mut e = engine(&tmp);

        let event = HeartbeatEvent::new(DeltaClass::Urgent, "memory".into(), "mem < 512MB".into());
        e.deliver_urgent(event).await;

        let queue_path = tmp.path().join(".vashion/heartbeat/urgent-queue.jsonl");
        assert!(queue_path.exists(), "urgent queue file must exist on disk");
        let content = fs::read_to_string(&queue_path).unwrap();
        assert!(
            content.contains("memory"),
            "event resource must appear in queue file"
        );
    }

    // ── overall_class_of helper ───────────────────────────────────────────────

    #[test]
    fn overall_class_urgent_wins() {
        let deltas = vec![
            ResourceDelta {
                resource: "a".into(),
                detail: "".into(),
                class: DeltaClass::Routine,
                bypass_cooldown: false,
            },
            ResourceDelta {
                resource: "b".into(),
                detail: "".into(),
                class: DeltaClass::Urgent,
                bypass_cooldown: false,
            },
        ];
        assert_eq!(overall_class_of(&deltas), DeltaClass::Urgent);
    }

    #[test]
    fn overall_class_notable_beats_routine() {
        let deltas = vec![
            ResourceDelta {
                resource: "a".into(),
                detail: "".into(),
                class: DeltaClass::Routine,
                bypass_cooldown: false,
            },
            ResourceDelta {
                resource: "b".into(),
                detail: "".into(),
                class: DeltaClass::Notable,
                bypass_cooldown: false,
            },
        ];
        assert_eq!(overall_class_of(&deltas), DeltaClass::Notable);
    }
}

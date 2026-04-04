// Co-authored by FORGE (Session: forge-vsh-005)
//! VSH-005: Auth & Model Registry — Model Registry
//!
//! The registry is the single source of truth for which models are available,
//! their capabilities, and which model is currently active. It is:
//!
//! - **Read-only at the public API boundary** — reads via `query()`, mutations
//!   only via explicit `set_active()` / `register()` calls.
//! - **Safe for concurrent access** — wrapped in `Arc<RwLock<_>>` so Brain
//!   can switch models at runtime without restarting Core.
//! - **Queried at Brain startup** to inform goal loop and context budgeting.
//! - **Included in auth checkpoint state** (active model name only).

use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fmt,
    sync::{Arc, RwLock},
};
use thiserror::Error;

use crate::auth::Provider;

// ---------------------------------------------------------------------------
// Cost tier
// ---------------------------------------------------------------------------

/// Relative cost classification for a model, used by Brain for context
/// budgeting decisions (e.g. prefer `Low` for short tasks, `High` for complex).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CostTier {
    /// No API cost — local inference (Ollama).
    Free,
    /// Cheap cloud tier (e.g. Haiku, GPT-4o-mini).
    Low,
    /// Mid-range cloud tier (e.g. Sonnet, GPT-4o).
    Medium,
    /// Premium cloud tier (e.g. Opus, o1).
    High,
}

impl fmt::Display for CostTier {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CostTier::Free => write!(f, "free"),
            CostTier::Low => write!(f, "low"),
            CostTier::Medium => write!(f, "medium"),
            CostTier::High => write!(f, "high"),
        }
    }
}

// ---------------------------------------------------------------------------
// Model info
// ---------------------------------------------------------------------------

/// Per-model metadata stored in the registry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    /// Model identifier as the provider expects it (e.g. `"claude-sonnet-4-6"`).
    pub name: String,
    /// Which provider hosts this model.
    pub provider: Provider,
    /// Maximum context window in tokens.
    pub context_window: u32,
    /// Relative cost classification.
    pub cost_tier: CostTier,
}

// ---------------------------------------------------------------------------
// Registry errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum RegistryError {
    #[error("model not found: {0}")]
    NotFound(String),

    #[error("registry is empty — no models registered")]
    Empty,
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/// In-memory model registry with `Arc<RwLock<_>>` for safe concurrent access.
///
/// Callers should clone the `Arc` and keep it; the `RwLock` guards the inner
/// state so that `set_active()` is visible immediately to all holders.
///
/// # Usage
/// ```
/// use vashion_core::model_registry::{ModelRegistry, ModelInfo, CostTier};
/// use vashion_core::auth::Provider;
/// use std::sync::Arc;
///
/// let registry = ModelRegistry::new_arc();
/// registry.write().unwrap().register(ModelInfo {
///     name: "claude-sonnet-4-6".to_string(),
///     provider: Provider::Anthropic,
///     context_window: 200_000,
///     cost_tier: CostTier::Medium,
/// });
/// registry.write().unwrap().set_active("claude-sonnet-4-6").unwrap();
/// let info = registry.read().unwrap().active_model().unwrap().clone();
/// assert_eq!(info.name, "claude-sonnet-4-6");
/// ```
#[derive(Debug, Default)]
pub struct ModelRegistry {
    models: HashMap<String, ModelInfo>,
    active: Option<String>,
}

impl ModelRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Wrap in `Arc<RwLock<_>>` for shared runtime use.
    pub fn new_arc() -> Arc<RwLock<Self>> {
        Arc::new(RwLock::new(Self::new()))
    }

    /// Register a model. Overwrites any existing entry with the same name.
    /// Writes require this explicit call — reads are always through `query()`.
    pub fn register(&mut self, info: ModelInfo) {
        tracing::debug!(
            name = %info.name,
            provider = %info.provider,
            context_window = info.context_window,
            cost_tier = %info.cost_tier,
            "model registered"
        );
        self.models.insert(info.name.clone(), info);
    }

    /// Switch the active model at runtime without restarting Core or Brain.
    ///
    /// Returns `Err(RegistryError::NotFound)` if the model is not registered.
    pub fn set_active(&mut self, name: &str) -> Result<(), RegistryError> {
        if !self.models.contains_key(name) {
            return Err(RegistryError::NotFound(name.to_string()));
        }
        tracing::info!(model = %name, "active model switched");
        self.active = Some(name.to_string());
        Ok(())
    }

    /// Query the currently active model. Returns `None` if no model has been
    /// set as active yet.
    pub fn active_model(&self) -> Option<&ModelInfo> {
        self.active.as_deref().and_then(|n| self.models.get(n))
    }

    /// Query a model by name. Read-only.
    pub fn get(&self, name: &str) -> Option<&ModelInfo> {
        self.models.get(name)
    }

    /// Return all registered models. Read-only.
    pub fn all(&self) -> impl Iterator<Item = &ModelInfo> {
        self.models.values()
    }

    /// Return models for a specific provider. Read-only.
    pub fn by_provider(&self, provider: &Provider) -> Vec<&ModelInfo> {
        self.models
            .values()
            .filter(|m| &m.provider == provider)
            .collect()
    }

    /// Name of the currently active model (for checkpoint serialization).
    pub fn active_name(&self) -> Option<&str> {
        self.active.as_deref()
    }
}

/// Default built-in models loaded at Brain startup.
///
/// Brain calls this at startup to seed the registry before querying for the
/// goal loop and context budgeting.
pub fn default_models() -> Vec<ModelInfo> {
    vec![
        ModelInfo {
            name: "claude-opus-4-6".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::High,
        },
        ModelInfo {
            name: "claude-sonnet-4-6".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::Medium,
        },
        ModelInfo {
            name: "claude-haiku-4-5".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::Low,
        },
        ModelInfo {
            name: "gpt-4o".to_string(),
            provider: Provider::OpenAI,
            context_window: 128_000,
            cost_tier: CostTier::Medium,
        },
        ModelInfo {
            name: "gpt-4o-mini".to_string(),
            provider: Provider::OpenAI,
            context_window: 128_000,
            cost_tier: CostTier::Low,
        },
        ModelInfo {
            name: "llama3.2".to_string(),
            provider: Provider::Ollama,
            context_window: 128_000,
            cost_tier: CostTier::Free,
        },
    ]
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn seeded_registry() -> ModelRegistry {
        let mut reg = ModelRegistry::new();
        for m in default_models() {
            reg.register(m);
        }
        reg
    }

    #[test]
    fn register_and_query() {
        let reg = seeded_registry();
        let m = reg.get("claude-sonnet-4-6").expect("should be registered");
        assert_eq!(m.provider, Provider::Anthropic);
        assert_eq!(m.context_window, 200_000);
        assert_eq!(m.cost_tier, CostTier::Medium);
    }

    #[test]
    fn set_active_and_retrieve() {
        let mut reg = seeded_registry();
        reg.set_active("gpt-4o").unwrap();
        let active = reg.active_model().expect("should have active model");
        assert_eq!(active.name, "gpt-4o");
    }

    #[test]
    fn set_active_unknown_model_errors() {
        let mut reg = seeded_registry();
        let err = reg.set_active("nonexistent-model").unwrap_err();
        assert!(matches!(err, RegistryError::NotFound(_)));
    }

    #[test]
    fn no_active_returns_none() {
        let reg = seeded_registry();
        assert!(reg.active_model().is_none());
    }

    #[test]
    fn runtime_switch_without_restart() {
        let arc = ModelRegistry::new_arc();
        {
            let mut reg = arc.write().unwrap();
            for m in default_models() {
                reg.register(m);
            }
            reg.set_active("claude-haiku-4-5").unwrap();
        }
        // Simulate Brain reading the active model concurrently.
        {
            let reg = arc.read().unwrap();
            assert_eq!(reg.active_model().unwrap().name, "claude-haiku-4-5");
        }
        // Switch without any restart.
        {
            let mut reg = arc.write().unwrap();
            reg.set_active("gpt-4o-mini").unwrap();
        }
        {
            let reg = arc.read().unwrap();
            assert_eq!(reg.active_model().unwrap().name, "gpt-4o-mini");
        }
    }

    #[test]
    fn by_provider_filter() {
        let reg = seeded_registry();
        let anthropic = reg.by_provider(&Provider::Anthropic);
        assert_eq!(anthropic.len(), 3);
        let ollama = reg.by_provider(&Provider::Ollama);
        assert_eq!(ollama.len(), 1);
    }

    #[test]
    fn all_models_count() {
        let reg = seeded_registry();
        assert_eq!(reg.all().count(), 6);
    }
}

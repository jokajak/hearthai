//! What one stage tells the next about how it ended.
//!
//! A stage reports a fixed code, never a message: the fetcher knows what went
//! wrong at the network level, and that knowledge must not become text the
//! model reads.

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::envelope::ErrorCode;
use crate::{ContractError, Result};

pub const STAGE_VERSION: u32 = 1;
/// File name of the stage outcome inside the handoff directory.
pub const STAGE_FILE: &str = "stage.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Fetch,
    Inspect,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StageOutcome {
    pub stage_version: u32,
    pub stage: Stage,
    pub run_id: String,
    /// `None` on success; a fixed public code otherwise.
    pub code: Option<ErrorCode>,
}

impl StageOutcome {
    pub fn ok(stage: Stage, run_id: impl Into<String>) -> Self {
        Self {
            stage_version: STAGE_VERSION,
            stage,
            run_id: run_id.into(),
            code: None,
        }
    }

    pub fn failed(stage: Stage, run_id: impl Into<String>, code: ErrorCode) -> Self {
        Self {
            stage_version: STAGE_VERSION,
            stage,
            run_id: run_id.into(),
            code: Some(code),
        }
    }

    pub fn write(&self, directory: &Path) -> Result<()> {
        fs::create_dir_all(directory)
            .map_err(|error| ContractError::new(format!("stage directory: {error}")))?;
        let encoded = serde_json::to_vec(self).map_err(|error| {
            ContractError::new(format!("stage outcome could not be serialized: {error}"))
        })?;
        fs::write(directory.join(STAGE_FILE), encoded)
            .map_err(|error| ContractError::new(format!("stage outcome: {error}")))?;
        Ok(())
    }

    pub fn load(directory: &Path, expected_run_id: &str) -> Result<Self> {
        let encoded = fs::read(directory.join(STAGE_FILE))
            .map_err(|error| ContractError::new(format!("stage outcome is unreadable: {error}")))?;
        let outcome: Self = serde_json::from_slice(&encoded)
            .map_err(|error| ContractError::new(format!("stage outcome is not valid: {error}")))?;
        if outcome.stage_version != STAGE_VERSION {
            return Err(ContractError::new("unsupported stage_version"));
        }
        if outcome.run_id != expected_run_id {
            return Err(ContractError::new(
                "stage outcome belongs to a different run",
            ));
        }
        Ok(outcome)
    }
}

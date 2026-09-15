//! What one stage tells the next about how it ended.
//!
//! A stage reports a fixed code, never a message: the fetcher knows what went
//! wrong at the network level, and that knowledge must not become text the
//! model reads. Writing and reading this file belongs to [`crate::handoff`],
//! which is what authenticates it.

use serde::{Deserialize, Serialize};

use crate::envelope::ErrorCode;
use crate::{ContractError, Result};

pub const STAGE_VERSION: u32 = 1;

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
    /// File name of the stage outcome inside the handoff directory.
    pub const FILE: &'static str = "stage.json";

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

    /// Confirm this outcome is one this build understands and belongs to this run.
    pub fn check(&self, expected_run_id: &str) -> Result<()> {
        if self.stage_version != STAGE_VERSION {
            return Err(ContractError::new("unsupported stage_version"));
        }
        if self.run_id != expected_run_id {
            return Err(ContractError::new(
                "stage outcome belongs to a different run",
            ));
        }
        if self.stage != Stage::Fetch {
            return Err(ContractError::new(
                "handoff was not written by the fetch stage",
            ));
        }
        Ok(())
    }
}

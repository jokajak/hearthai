//! Versioned wire contracts for HearthAI webfetch.
//!
//! Everything a caller, a worker stage, or a future consumer puts on the wire is
//! defined here and validated the same way in both directions. The JSON schemas
//! in `../schemas/` and the fixtures in `../fixtures/` are the language-neutral
//! copy of these rules; `tests/fixtures.rs` keeps the two honest.
//!
//! Two rules shape the types more than anything else:
//!
//! * A failure never carries remote text. Error messages are constants selected
//!   by a fixed code, so there is no field an implementation could accidentally
//!   fill with a page title, a URL, or a worker backtrace.
//! * Control fields are server-owned. Source-provided JSON that happens to look
//!   like an envelope stays text inside `data.content`, because the envelope is
//!   built by the result gate from typed values, never merged from the response.

pub mod artifact;
pub mod envelope;
pub mod handoff;
pub mod limits;
pub mod media;
pub mod stage;
pub mod web_fetch;

use std::fmt;

/// A value does not conform to a public or stage wire contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContractError(String);

impl ContractError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    pub fn message(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ContractError {}

pub type Result<T> = std::result::Result<T, ContractError>;

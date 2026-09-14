//! Contract failures name the field that was wrong and never the value that was in it.
//!
//! A worker result travels through these types on its way to the model, so an
//! error string is model-visible. Echoing the offending value here would be a
//! way for a response body to reach model context through a validation failure,
//! which is exactly what the tool exists to prevent.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContractError {
    pub path: String,
    pub detail: &'static str,
}

impl ContractError {
    pub fn new(path: impl Into<String>, detail: &'static str) -> Self {
        Self { path: path.into(), detail }
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {}", self.path, self.detail)
    }
}

impl std::error::Error for ContractError {}

pub type Result<T> = std::result::Result<T, ContractError>;

pub(crate) fn invalid<T>(path: impl Into<String>, detail: &'static str) -> Result<T> {
    Err(ContractError::new(path, detail))
}

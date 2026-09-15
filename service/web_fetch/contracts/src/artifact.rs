//! The fetch-to-inspection handoff.
//!
//! The fetcher writes a directory containing `artifact.json` and one body file
//! and then exits. The inspector reads that directory and is required to prove,
//! before it decodes a single byte, that what it has is the artifact this run
//! produced and that nothing changed it in between.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::web_fetch::Format;
use crate::{ContractError, Result};

pub const ARTIFACT_VERSION: u32 = 1;
/// File name of the artifact description inside the handoff directory.
pub const ARTIFACT_FILE: &str = "artifact.json";
/// File name of the stored wire body inside the handoff directory.
pub const BODY_FILE: &str = "body.bin";

/// Description of one completed fetch. It travels with the body and binds it to
/// this run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FetchArtifact {
    pub artifact_version: u32,
    pub run_id: String,
    pub source_id: String,
    /// Digest of the requested URL. Audit records keep this; they never keep the URL.
    pub requested_url_sha256: String,
    /// The URL actually fetched after any redirects. Remote data: inspected like
    /// the body before it can appear in a result.
    pub final_url: String,
    pub http_status: u16,
    pub content_type: Option<String>,
    pub content_encoding: Option<String>,
    pub redirects: u32,
    pub retrieved_at: String,
    /// The output format the caller asked for, carried across the boundary so
    /// the offline stage needs no second copy of the request.
    pub format: Format,
    pub body_bytes: usize,
    pub body_sha256: String,
    /// Which destination policy profile the fetcher ran under.
    pub policy_profile: String,
}

impl FetchArtifact {
    pub fn write(&self, directory: &Path, body: &[u8]) -> Result<()> {
        if body.len() != self.body_bytes || sha256_hex(body) != self.body_sha256 {
            return Err(ContractError::new(
                "artifact does not describe the body it is written with",
            ));
        }
        fs::create_dir_all(directory)
            .map_err(|error| ContractError::new(format!("artifact directory: {error}")))?;
        fs::write(directory.join(BODY_FILE), body)
            .map_err(|error| ContractError::new(format!("artifact body: {error}")))?;
        let encoded = serde_json::to_vec(self).map_err(|error| {
            ContractError::new(format!("artifact could not be serialized: {error}"))
        })?;
        fs::write(directory.join(ARTIFACT_FILE), encoded)
            .map_err(|error| ContractError::new(format!("artifact description: {error}")))?;
        Ok(())
    }

    /// Load an artifact and its body, refusing anything that is not this run's.
    ///
    /// The digest check is what makes a replayed, swapped, or half-written
    /// artifact a failure instead of a scan of the wrong bytes.
    pub fn load(directory: &Path, expected_run_id: &str) -> Result<(Self, Vec<u8>)> {
        let described = fs::read(directory.join(ARTIFACT_FILE)).map_err(|error| {
            ContractError::new(format!("artifact description is unreadable: {error}"))
        })?;
        let artifact: Self = serde_json::from_slice(&described).map_err(|error| {
            ContractError::new(format!("artifact description is not valid: {error}"))
        })?;
        if artifact.artifact_version != ARTIFACT_VERSION {
            return Err(ContractError::new(format!(
                "unsupported artifact_version: {}",
                artifact.artifact_version
            )));
        }
        if artifact.run_id != expected_run_id {
            return Err(ContractError::new("artifact belongs to a different run"));
        }
        let body = fs::read(body_path(directory))
            .map_err(|error| ContractError::new(format!("artifact body is unreadable: {error}")))?;
        if body.len() != artifact.body_bytes {
            return Err(ContractError::new(
                "artifact body length does not match its description",
            ));
        }
        if sha256_hex(&body) != artifact.body_sha256 {
            return Err(ContractError::new(
                "artifact body digest does not match its description",
            ));
        }
        Ok((artifact, body))
    }
}

pub fn body_path(directory: &Path) -> PathBuf {
    directory.join(BODY_FILE)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push_str(&format!("{byte:02x}"));
    }
    encoded
}

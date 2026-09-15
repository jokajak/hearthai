//! What one fetch produced, as data.
//!
//! Reading and writing it is [`crate::handoff`]'s job, because the integrity
//! rules belong to the directory the two stages share rather than to this
//! description of a response.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::web_fetch::Format;

pub const ARTIFACT_VERSION: u32 = 1;

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

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push_str(&format!("{byte:02x}"));
    }
    encoded
}

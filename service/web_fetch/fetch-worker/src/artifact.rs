//! The handoff between the two stages.
//!
//! The fetcher writes a response once and exits; the inspector reads it in a
//! pod with no network. Nothing about that handoff is trusted on the strength
//! of a filename: the artifact carries the run it belongs to and the digest of
//! its own bytes, and the reader checks both. That is what makes a stale
//! artifact, an artifact from another run, and an artifact that changed after
//! it was written all fail the same way.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::FetchError;

pub const STAGE: &str = "fetch";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResponseArtifact {
    pub run_id: String,
    pub source_id: String,
    pub stage: String,
    pub final_url: String,
    pub http_status: u16,
    pub media_type: String,
    pub charset: Option<String>,
    pub content_encoding: Option<String>,
    pub retrieved_at: String,
    pub digest: String,
    pub length: usize,
}

impl ResponseArtifact {
    fn to_json(&self) -> Value {
        json!({
            "run_id": self.run_id,
            "source_id": self.source_id,
            "stage": self.stage,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "media_type": self.media_type,
            "charset": self.charset,
            "content_encoding": self.content_encoding,
            "retrieved_at": self.retrieved_at,
            "digest": self.digest,
            "length": self.length,
        })
    }

    fn from_json(value: &Value) -> Option<Self> {
        let object = value.as_object()?;
        let text = |key: &str| object.get(key)?.as_str().map(str::to_owned);
        let optional = |key: &str| match object.get(key) {
            Some(Value::Null) | None => Some(None),
            Some(Value::String(value)) => Some(Some(value.clone())),
            _ => None,
        };
        Some(Self {
            run_id: text("run_id")?,
            source_id: text("source_id")?,
            stage: text("stage")?,
            final_url: text("final_url")?,
            http_status: u16::try_from(object.get("http_status")?.as_u64()?).ok()?,
            media_type: text("media_type")?,
            charset: optional("charset")?,
            content_encoding: optional("content_encoding")?,
            retrieved_at: text("retrieved_at")?,
            digest: text("digest")?,
            length: usize::try_from(object.get("length")?.as_u64()?).ok()?,
        })
    }
}

pub fn digest_of(body: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(body);
    hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect()
}

/// A run-scoped opaque identifier. Derived from the run so it is stable within
/// it, hashed so it is not a path, and never dereferenceable by a later call.
pub fn source_id_for(run_id: &str, sequence: u32) -> String {
    let mut hasher = Sha256::new();
    hasher.update(run_id.as_bytes());
    hasher.update(b":");
    hasher.update(sequence.to_be_bytes());
    let hex: String = hasher.finalize().iter().take(8).map(|byte| format!("{byte:02x}")).collect();
    format!("source-{hex}")
}

fn run_directory(root: &Path, run_id: &str) -> PathBuf {
    root.join(run_id)
}

/// Writes the body and its metadata, then makes both read-only.
///
/// The inspector never shares a writable volume with a live fetcher: this
/// returns only after the bytes are complete on disk, and the process that
/// wrote them is expected to exit before the next stage starts.
pub fn write(root: &Path, artifact: &ResponseArtifact, body: &[u8]) -> Result<(), FetchError> {
    let directory = run_directory(root, &artifact.run_id);
    fs::create_dir_all(&directory).map_err(|_| FetchError::Storage)?;
    let body_path = directory.join("body");
    fs::write(&body_path, body).map_err(|_| FetchError::Storage)?;
    fs::write(directory.join("meta.json"), artifact.to_json().to_string())
        .map_err(|_| FetchError::Storage)?;
    for name in ["body", "meta.json"] {
        let path = directory.join(name);
        let mut permissions = fs::metadata(&path).map_err(|_| FetchError::Storage)?.permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&path, permissions).map_err(|_| FetchError::Storage)?;
    }
    Ok(())
}

/// Reads the artifact the caller expects, or fails.
///
/// `run_id` is supplied by the substrate, not read out of the artifact, so an
/// artifact that names a different run is a cross-run access rather than a
/// successful read of someone else's response.
pub fn read_bound(root: &Path, run_id: &str) -> Result<(ResponseArtifact, Vec<u8>), FetchError> {
    let directory = run_directory(root, run_id);
    let meta = fs::read_to_string(directory.join("meta.json")).map_err(|_| FetchError::Storage)?;
    let value: Value = serde_json::from_str(&meta).map_err(|_| FetchError::Storage)?;
    let artifact = ResponseArtifact::from_json(&value).ok_or(FetchError::Storage)?;
    if artifact.run_id != run_id || artifact.stage != STAGE {
        return Err(FetchError::Storage);
    }
    let body = fs::read(directory.join("body")).map_err(|_| FetchError::Storage)?;
    if body.len() != artifact.length || digest_of(&body) != artifact.digest {
        return Err(FetchError::Storage);
    }
    Ok((artifact, body))
}

/// Idempotent, because it has to run on success, failure, cancellation and
/// after a controller restart that does not know which of those happened.
pub fn remove(root: &Path, run_id: &str) -> Result<(), FetchError> {
    let directory = run_directory(root, run_id);
    match fs::remove_dir_all(&directory) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(FetchError::Storage),
    }
}

/// What survives the run.
///
/// Deliberately not the artifact: no URL, no body, no headers, no matched text,
/// no worker exception. Counts and timings answer the operational questions
/// without keeping the response around to answer them with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuditRecord {
    pub run_id: String,
    pub outcome: String,
    pub http_status: Option<u16>,
    pub wire_bytes: usize,
    pub redirects: u8,
    pub elapsed_ms: u64,
}

impl AuditRecord {
    pub fn to_json(&self) -> Value {
        json!({
            "run_id": self.run_id,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "wire_bytes": self.wire_bytes,
            "redirects": self.redirects,
            "elapsed_ms": self.elapsed_ms,
        })
    }
}

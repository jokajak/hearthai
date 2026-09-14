//! Fake detectors and scratch artifacts for the pipeline tests.
//!
//! The detectors record every buffer they were asked to scan, which is how a
//! test can assert the difference between "the chain stopped" and "the chain
//! carried on and happened to return the right thing".

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use hearthai_web_fetch_inspector::{Detector, Verdict};
use hearthai_web_fetch_worker::artifact::{self, ResponseArtifact, digest_of, source_id_for};

pub struct RecordingDetector {
    verdict: Box<dyn Fn(&str) -> Verdict + Send + Sync>,
    pub scanned: Mutex<Vec<String>>,
}

impl RecordingDetector {
    pub fn never_matches() -> Self {
        Self::new(|_| Verdict::NoMatch)
    }

    pub fn always_fails() -> Self {
        Self::new(|_| Verdict::Error)
    }

    pub fn matching(needle: &'static str) -> Self {
        Self::new(move |text| if text.contains(needle) { Verdict::Match } else { Verdict::NoMatch })
    }

    pub fn failing_on(needle: &'static str) -> Self {
        Self::new(move |text| if text.contains(needle) { Verdict::Error } else { Verdict::NoMatch })
    }

    pub fn new(verdict: impl Fn(&str) -> Verdict + Send + Sync + 'static) -> Self {
        Self { verdict: Box::new(verdict), scanned: Mutex::new(Vec::new()) }
    }

    pub fn scan_count(&self) -> usize {
        self.scanned.lock().unwrap().len()
    }
}

impl Detector for RecordingDetector {
    fn scan(&self, bytes: &[u8]) -> Verdict {
        let text = String::from_utf8_lossy(bytes).into_owned();
        let verdict = (self.verdict)(&text);
        self.scanned.lock().unwrap().push(text);
        verdict
    }

    fn policy_id(&self) -> &str {
        "web-content-v1"
    }
}

pub struct Scratch(pub PathBuf);

impl Scratch {
    pub fn new(name: &str) -> Self {
        let path = std::env::temp_dir().join(format!("hearthai-inspect-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    pub fn root(&self) -> &Path {
        &self.0
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub struct Stored {
    pub media_type: String,
    pub charset: Option<String>,
    pub content_encoding: Option<String>,
    pub http_status: u16,
    pub final_url: String,
}

impl Default for Stored {
    fn default() -> Self {
        Self {
            media_type: "text/html".into(),
            charset: Some("utf-8".into()),
            content_encoding: None,
            http_status: 200,
            final_url: "https://example.com/page".into(),
        }
    }
}

/// Writes a response artifact the way the fetch stage would have.
pub fn store(scratch: &Scratch, run_id: &str, body: &[u8], stored: Stored) {
    let artifact = ResponseArtifact {
        run_id: run_id.to_owned(),
        source_id: source_id_for(run_id, 0),
        stage: artifact::STAGE.to_owned(),
        final_url: stored.final_url,
        http_status: stored.http_status,
        media_type: stored.media_type,
        charset: stored.charset,
        content_encoding: stored.content_encoding,
        retrieved_at: "2026-09-12T12:00:00Z".into(),
        digest: digest_of(body),
        length: body.len(),
    };
    artifact::write(scratch.root(), &artifact, body).unwrap();
}

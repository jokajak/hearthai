//! The stage handoff: binding, tampering, replay and cleanup.

use std::fs;
use std::path::PathBuf;

use hearthai_web_fetch_worker::FetchError;
use hearthai_web_fetch_worker::artifact::{self, AuditRecord, ResponseArtifact, digest_of, source_id_for};

struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let path = std::env::temp_dir().join(format!("hearthai-webfetch-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn artifact_for(run_id: &str, body: &[u8]) -> ResponseArtifact {
    ResponseArtifact {
        run_id: run_id.to_owned(),
        source_id: source_id_for(run_id, 0),
        stage: artifact::STAGE.to_owned(),
        final_url: "https://example.com/page".into(),
        http_status: 200,
        media_type: "text/html".into(),
        charset: Some("utf-8".into()),
        content_encoding: None,
        retrieved_at: "2026-09-12T12:00:00Z".into(),
        digest: digest_of(body),
        length: body.len(),
    }
}

#[test]
fn a_written_artifact_reads_back_under_its_own_run() {
    let scratch = Scratch::new("roundtrip");
    let body = b"<h1>Example</h1>".to_vec();
    let written = artifact_for("run-a", &body);
    artifact::write(&scratch.0, &written, &body).unwrap();

    let (read, bytes) = artifact::read_bound(&scratch.0, "run-a").unwrap();
    assert_eq!(read, written);
    assert_eq!(bytes, body);
}

#[test]
fn another_runs_artifact_is_not_readable_as_this_ones() {
    let scratch = Scratch::new("crossrun");
    let body = b"other run".to_vec();
    artifact::write(&scratch.0, &artifact_for("run-a", &body), &body).unwrap();

    // The directory name is not the binding: even renamed into place under a
    // second run, the artifact still names the run it was made for.
    fs::rename(scratch.0.join("run-a"), scratch.0.join("run-b")).unwrap();
    assert_eq!(artifact::read_bound(&scratch.0, "run-b").unwrap_err(), FetchError::Storage);
}

#[test]
fn an_artifact_that_changed_after_it_was_written_is_refused() {
    let scratch = Scratch::new("tamper");
    let body = b"original response".to_vec();
    artifact::write(&scratch.0, &artifact_for("run-a", &body), &body).unwrap();

    let body_path = scratch.0.join("run-a").join("body");
    let mut permissions = fs::metadata(&body_path).unwrap().permissions();
    #[allow(clippy::permissions_set_readonly_false)]
    permissions.set_readonly(false);
    fs::set_permissions(&body_path, permissions).unwrap();
    fs::write(&body_path, b"replaced response").unwrap();

    assert_eq!(artifact::read_bound(&scratch.0, "run-a").unwrap_err(), FetchError::Storage);
}

#[test]
fn a_truncated_artifact_is_refused_rather_than_inspected_as_a_prefix() {
    let scratch = Scratch::new("truncated");
    let body = b"a complete response body".to_vec();
    artifact::write(&scratch.0, &artifact_for("run-a", &body), &body).unwrap();

    let body_path = scratch.0.join("run-a").join("body");
    let mut permissions = fs::metadata(&body_path).unwrap().permissions();
    #[allow(clippy::permissions_set_readonly_false)]
    permissions.set_readonly(false);
    fs::set_permissions(&body_path, permissions).unwrap();
    fs::write(&body_path, &body[..4]).unwrap();

    assert_eq!(artifact::read_bound(&scratch.0, "run-a").unwrap_err(), FetchError::Storage);
}

#[test]
fn a_missing_artifact_is_a_failure_not_an_empty_response() {
    let scratch = Scratch::new("missing");
    assert_eq!(artifact::read_bound(&scratch.0, "run-a").unwrap_err(), FetchError::Storage);
}

#[test]
fn cleanup_runs_whether_or_not_there_is_anything_to_clean() {
    let scratch = Scratch::new("cleanup");
    let body = b"temporary".to_vec();
    artifact::write(&scratch.0, &artifact_for("run-a", &body), &body).unwrap();

    artifact::remove(&scratch.0, "run-a").unwrap();
    assert!(!scratch.0.join("run-a").exists());
    // Again, as a restarted controller that does not know what already happened.
    artifact::remove(&scratch.0, "run-a").unwrap();
    assert_eq!(artifact::read_bound(&scratch.0, "run-a").unwrap_err(), FetchError::Storage);
}

#[test]
fn a_source_id_is_opaque_run_scoped_and_not_a_handle() {
    let first = source_id_for("run-a", 0);
    let second = source_id_for("run-b", 0);
    assert_ne!(first, second);
    assert_eq!(first, source_id_for("run-a", 0), "the identifier is not stable within a run");
    assert_ne!(first, source_id_for("run-a", 1));
    for id in [&first, &second] {
        assert!(id.starts_with("source-"));
        assert!(!id.contains('/') && !id.contains("run-"), "{id} leaks its origin");
        assert!(id.len() <= 64);
    }
}

#[test]
fn the_durable_record_keeps_counts_and_not_the_response() {
    let record = AuditRecord {
        run_id: "run-a".into(),
        outcome: "content_rejected".into(),
        http_status: Some(200),
        wire_bytes: 4_096,
        redirects: 1,
        elapsed_ms: 812,
    };
    let serialized = record.to_json().to_string();
    for absent in ["example.com", "https://", "text/html", "<h1>", "source-"] {
        assert!(!serialized.contains(absent), "the audit record carried {absent}: {serialized}");
    }
    assert!(serialized.contains("4096") && serialized.contains("812"));
}

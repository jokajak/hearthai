//! Replay the shared cross-language fixtures.
//!
//! `service/web_fetch/fixtures/manifest.json` is the single list of cases. The
//! Python control-plane adapter runs the same file, so a disagreement about any
//! one document fails on one side or the other rather than becoming a silent
//! difference between the validator that admits a request and the validator
//! that admits a result.

use std::path::{Path, PathBuf};

use hearthai_web_fetch_contracts::{WebFetchEnvelope, WebFetchRequest};
use serde_json::Value;

fn fixture_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("fixtures")
}

fn manifest() -> Value {
    serde_json::from_str(&std::fs::read_to_string(fixture_root().join("manifest.json")).unwrap()).unwrap()
}

fn cases(section: &str) -> Vec<(String, bool, String)> {
    manifest()[section]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| {
            (
                case["file"].as_str().unwrap().to_owned(),
                case["valid"].as_bool().unwrap(),
                case["reason"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

fn read(file: &str) -> String {
    std::fs::read_to_string(fixture_root().join(file)).unwrap()
}

#[test]
fn request_fixtures_match_the_manifest() {
    let cases = cases("requests");
    assert!(cases.len() >= 12, "the shared request corpus lost cases");
    for (file, valid, reason) in cases {
        let parsed = WebFetchRequest::parse(&read(&file));
        assert_eq!(parsed.is_ok(), valid, "{file}: {reason}");
    }
}

#[test]
fn envelope_fixtures_match_the_manifest() {
    let cases = cases("envelopes");
    assert!(cases.len() >= 20, "the shared envelope corpus lost cases");
    for (file, valid, reason) in cases {
        let parsed = WebFetchEnvelope::parse(&read(&file));
        assert_eq!(parsed.is_ok(), valid, "{file}: {reason}");
    }
}

#[test]
fn valid_fixtures_round_trip_through_the_typed_form() {
    for (file, valid, _) in cases("envelopes") {
        if !valid {
            continue;
        }
        let original: Value = serde_json::from_str(&read(&file)).unwrap();
        let envelope = WebFetchEnvelope::parse(&read(&file)).unwrap();
        assert_eq!(envelope.to_json(), original, "{file} did not survive a round trip");
    }
}

#[test]
fn a_rejection_error_never_carries_the_text_that_caused_it() {
    // The fixture's message names a rule and quotes the matched string. Both
    // are things the response could influence, so the parse has to fail rather
    // than pass that message on to the model.
    let document = read("envelopes/invalid-error-message-carries-source.json");
    let failure = WebFetchEnvelope::parse(&document).unwrap_err().to_string();
    for leaked in ["ignore all previous instructions", "example.com", "prompt_injection_v1"] {
        assert!(!failure.contains(leaked), "the contract error repeated source text: {failure}");
    }
}

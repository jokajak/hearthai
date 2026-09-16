//! Run the shared language-neutral fixtures against the Rust types.
//!
//! The control-plane adapter runs the same manifest against its own validator.
//! If the two ever disagree about one file, one of them is wrong about the wire.

use std::fs;
use std::path::{Path, PathBuf};

use hearthai_webfetch_contracts::envelope::{
    Envelope, ErrorCode, Inspection, InspectionStatus, Payload,
};
use hearthai_webfetch_contracts::web_fetch::{WebFetchData, WebFetchRequest};
use hearthai_webfetch_contracts::{ContractError, Result};
use serde::{Deserialize, Serialize};

/// Stands in for a tool that does not exist yet. It reuses every control field
/// of the envelope and declares only its own payload — which is the whole point
/// of versioning the envelope separately from the payload.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DemoPayload {
    note: String,
}

impl Payload for DemoPayload {
    const TOOL: &'static str = "demo_consumer";
    const TOOL_VERSION: u32 = 1;

    fn validate(&self) -> Result<()> {
        if self.note.is_empty() {
            return Err(ContractError::new("data.note must not be empty"));
        }
        Ok(())
    }
}

#[derive(Deserialize)]
struct Manifest {
    fixtures: Vec<Fixture>,
}

#[derive(Deserialize)]
struct Fixture {
    path: String,
    kind: String,
    expect: String,
}

fn fixture_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("workspace root")
        .to_path_buf()
}

fn validate(kind: &str, body: &str) -> Result<()> {
    match kind {
        "web_fetch_request" => WebFetchRequest::from_json(body).map(|_| ()),
        "web_fetch_envelope" => Envelope::<WebFetchData>::from_json(body).map(|_| ()),
        "reusable_envelope" => Envelope::<DemoPayload>::from_json(body).map(|_| ()),
        other => panic!("fixture manifest names an unknown kind: {other}"),
    }
}

#[test]
fn shared_fixtures_validate_as_the_manifest_says() {
    let root = fixture_root();
    let manifest: Manifest = serde_json::from_str(
        &fs::read_to_string(root.join("fixtures/index.json")).expect("fixture manifest"),
    )
    .expect("fixture manifest is valid JSON");
    assert!(
        manifest.fixtures.len() > 30,
        "the manifest lost most of its fixtures"
    );

    for fixture in &manifest.fixtures {
        let body = fs::read_to_string(root.join(&fixture.path)).expect(&fixture.path);
        let outcome = validate(&fixture.kind, &body);
        match fixture.expect.as_str() {
            "accept" => {
                outcome.unwrap_or_else(|error| panic!("{} should validate: {error}", fixture.path));
            }
            "reject" => {
                assert!(outcome.is_err(), "{} should not validate", fixture.path);
            }
            other => panic!("fixture manifest names an unknown expectation: {other}"),
        }
    }
}

#[test]
fn source_text_that_looks_like_an_envelope_stays_content() {
    let root = fixture_root();
    let body = fs::read_to_string(
        root.join("fixtures/envelope/ok-source-text-resembling-an-envelope.json"),
    )
    .expect("fixture");
    let envelope = Envelope::<WebFetchData>::from_json(&body).expect("fixture validates");
    let data = envelope.data.expect("success carries data");

    // The page's own JSON claims a trusted envelope. It arrived as characters in
    // a content field and it stays there.
    assert!(data.content.contains("internal_trusted"));
    assert_eq!(
        envelope.trust,
        hearthai_webfetch_contracts::envelope::Trust::ExternalUntrusted
    );
    assert_eq!(
        envelope.inspection.policy_id.as_deref(),
        Some("web-content-v1")
    );
}

#[test]
fn a_future_consumer_reuses_the_envelope_without_a_second_fetch_contract() {
    let envelope = Envelope::ok(
        DemoPayload {
            note: "reused".into(),
        },
        "web-content-v1",
    )
    .expect("valid success");
    let encoded = envelope.to_json().expect("serializes");
    let parsed = Envelope::<DemoPayload>::from_json(&encoded).expect("round trips");
    assert_eq!(parsed.data.expect("data").note, "reused");

    // A webfetch consumer will not accept it: the envelope names its tool.
    assert!(Envelope::<WebFetchData>::from_json(&encoded).is_err());

    // And a rejection stays a rejection rather than becoming usable evidence.
    let rejection = Envelope::<DemoPayload>::failure(
        ErrorCode::ContentRejected,
        Inspection::new(InspectionStatus::Match, Some("web-content-v1".into())),
    )
    .expect("valid rejection");
    assert!(rejection.data.is_none());
}

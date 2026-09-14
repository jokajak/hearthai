//! A second consumer of the envelope, written the way research will have to be.
//!
//! The point of defining the envelope separately from webfetch is that the next
//! tool inherits the admission rules instead of restating them. This test is
//! the falsifiable version of that claim: a payload type that knows nothing
//! about fetching still cannot forge a success, cannot be handed a rejection as
//! if it were evidence, and cannot read a body out of a withheld response.

use hearthai_web_fetch_contracts::{
    ErrorCode, Inspection, InspectionStatus, Status, ToolPayload, ToolResultEnvelope, WebFetchEnvelope,
};
use serde_json::{Value, json};

/// Stands in for a future `web_research:v1` payload.
#[derive(Debug, Clone, PartialEq, Eq)]
struct DigestPayload {
    headline: String,
    source_ids: Vec<String>,
}

impl ToolPayload for DigestPayload {
    const TOOL: &'static str = "example_digest";
    const VERSION: u32 = 1;

    fn from_json(value: &Value) -> hearthai_web_fetch_contracts::Result<Self> {
        let object = value
            .as_object()
            .ok_or_else(|| hearthai_web_fetch_contracts::ContractError::new("data", "must be an object"))?;
        let headline = object.get("headline").and_then(Value::as_str).ok_or_else(|| {
            hearthai_web_fetch_contracts::ContractError::new("data.headline", "is required")
        })?;
        let source_ids = object
            .get("source_ids")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                hearthai_web_fetch_contracts::ContractError::new("data.source_ids", "is required")
            })?
            .iter()
            .filter_map(|item| item.as_str().map(str::to_owned))
            .collect();
        Ok(Self { headline: headline.to_owned(), source_ids })
    }

    fn to_json(&self) -> Value {
        json!({"headline": self.headline, "source_ids": self.source_ids})
    }
}

type DigestEnvelope = ToolResultEnvelope<DigestPayload>;

#[test]
fn a_new_consumer_reuses_the_envelope_without_a_new_fetch_contract() {
    let envelope = DigestEnvelope::admitted(
        "digest-v1",
        DigestPayload { headline: "Two sources agree.".into(), source_ids: vec!["source-1".into()] },
    );
    let document = serde_json::to_string(&envelope.to_json()).unwrap();

    let reparsed = DigestEnvelope::parse(&document).unwrap();
    assert_eq!(reparsed.status, Status::Ok);
    assert_eq!(reparsed.data.unwrap().source_ids, ["source-1"]);
    assert_eq!(reparsed.inspection.status, InspectionStatus::NoMatch);
}

#[test]
fn a_consumer_cannot_read_one_tools_envelope_as_another() {
    let webfetch = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("fixtures/envelopes/ok-markdown.json"),
    )
    .unwrap();
    assert!(DigestEnvelope::parse(&webfetch).is_err());
    assert!(WebFetchEnvelope::parse(&webfetch).is_ok());
}

#[test]
fn a_rejection_carries_no_payload_a_consumer_could_treat_as_evidence() {
    let envelope = DigestEnvelope::rejected("digest-v1");
    assert!(envelope.data.is_none());
    assert_eq!(envelope.error.unwrap().code, ErrorCode::ContentRejected);

    // And the serialized form is still a rejection after a round trip: there is
    // no representation of "rejected, but here is the body anyway".
    let document = serde_json::to_string(&envelope.to_json()).unwrap();
    let reparsed = DigestEnvelope::parse(&document).unwrap();
    assert_eq!(reparsed.status, Status::Rejected);
    assert!(reparsed.data.is_none());
}

#[test]
fn a_failure_cannot_be_dressed_up_as_a_rejection_or_a_success() {
    // content_rejected means a rule matched; it is not available to a stage
    // that merely failed.
    assert!(DigestEnvelope::failed(ErrorCode::ContentRejected, Inspection::failed(None)).is_err());

    let failure =
        DigestEnvelope::failed(ErrorCode::InspectionFailed, Inspection::failed(Some("digest-v1".into())))
            .unwrap();
    assert_eq!(failure.status, Status::Error);
    assert!(failure.data.is_none());
    assert_eq!(failure.error.unwrap().message(), "Response inspection did not complete.");
}

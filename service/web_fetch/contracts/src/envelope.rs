//! The reusable versioned tool-result envelope.
//!
//! `web_fetch:v1` is its first user, but nothing here knows about fetching. A
//! later consumer - research is the expected one - carries its own payload type
//! through the same status, trust and inspection fields instead of inventing a
//! second result shape with a second set of rules about when content may be
//! admitted.
//!
//! The gate constructs these values; it does not copy them out of a worker's
//! stdout. Text from a fetched page that happens to look like an envelope
//! deserializes into `data.content`, a plain string, and cannot reach any field
//! below it.

use serde_json::{Map, Value, json};

use crate::error::{ContractError, Result, invalid};
use crate::json::{is_lower_identifier, require_exact_fields, require_object, require_string, require_u32};

pub const ENVELOPE_VERSION: u32 = 1;

const ENVELOPE_FIELDS: [&str; 8] =
    ["envelope_version", "tool", "tool_version", "status", "trust", "inspection", "data", "error"];

/// A typed result payload that the envelope can carry.
///
/// Implementing this is the whole cost of reusing the envelope: a consumer
/// supplies its tool identity and a validator for its own `data`, and inherits
/// the admission rules unchanged.
pub trait ToolPayload: Sized {
    const TOOL: &'static str;
    const VERSION: u32;

    fn from_json(value: &Value) -> Result<Self>;
    fn to_json(&self) -> Value;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    Rejected,
    Error,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Ok => "ok",
            Status::Rejected => "rejected",
            Status::Error => "error",
        }
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        match value {
            "ok" => Ok(Status::Ok),
            "rejected" => Ok(Status::Rejected),
            "error" => Ok(Status::Error),
            _ => invalid(path, "is not a known envelope status"),
        }
    }
}

/// One variant today. Fetched data never describes itself as anything else, and
/// the marker grants no permission either way; it labels provenance so a
/// consumer cannot flatten a response into unlabeled trusted instructions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trust {
    ExternalUntrusted,
}

impl Trust {
    pub fn as_str(self) -> &'static str {
        "external_untrusted"
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        match value {
            "external_untrusted" => Ok(Trust::ExternalUntrusted),
            _ => invalid(path, "is not a known trust marker"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InspectionStatus {
    NoMatch,
    Match,
    Failed,
    NotRun,
}

impl InspectionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            InspectionStatus::NoMatch => "no_match",
            InspectionStatus::Match => "match",
            InspectionStatus::Failed => "failed",
            InspectionStatus::NotRun => "not_run",
        }
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        match value {
            "no_match" => Ok(InspectionStatus::NoMatch),
            "match" => Ok(InspectionStatus::Match),
            "failed" => Ok(InspectionStatus::Failed),
            "not_run" => Ok(InspectionStatus::NotRun),
            _ => invalid(path, "is not a known inspection status"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inspection {
    pub status: InspectionStatus,
    /// A reference to the immutable policy the run was bound to internally, not
    /// an explanation and not a substitute for that binding.
    pub policy_id: Option<String>,
}

impl Inspection {
    pub fn no_match(policy_id: impl Into<String>) -> Self {
        Self { status: InspectionStatus::NoMatch, policy_id: Some(policy_id.into()) }
    }

    pub fn matched(policy_id: impl Into<String>) -> Self {
        Self { status: InspectionStatus::Match, policy_id: Some(policy_id.into()) }
    }

    pub fn failed(policy_id: Option<String>) -> Self {
        Self { status: InspectionStatus::Failed, policy_id }
    }

    pub fn not_run() -> Self {
        Self { status: InspectionStatus::NotRun, policy_id: None }
    }

    fn from_json(value: &Value, path: &str) -> Result<Self> {
        let object = require_object(value, path)?;
        require_exact_fields(object, &["status", "policy_id"], &[], path)?;
        let status = InspectionStatus::parse(
            require_string(&object["status"], &format!("{path}.status"), 32)?,
            &format!("{path}.status"),
        )?;
        let policy_id = match &object["policy_id"] {
            Value::Null => None,
            other => {
                let text = require_string(other, &format!("{path}.policy_id"), 64)?;
                if !is_lower_identifier(text, '-') {
                    return invalid(format!("{path}.policy_id"), "is not a policy identifier");
                }
                Some(text.to_owned())
            }
        };
        Ok(Self { status, policy_id })
    }

    fn to_json(&self) -> Value {
        json!({
            "status": self.status.as_str(),
            "policy_id": self.policy_id.clone().map(Value::String).unwrap_or(Value::Null),
        })
    }
}

/// Failure identities from the design's outcome table.
///
/// Each code owns one fixed message. A message is never composed from a body,
/// a title, a header, a URL, matched text, or a worker exception, so the
/// message is data the caller already knew rather than a channel out of the
/// sandbox.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    ContentRejected,
    InspectionFailed,
    UnsafeSource,
    UnsupportedContent,
    ResponseLimitExceeded,
    FetchFailed,
    DeadlineExceeded,
}

impl ErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::ContentRejected => "content_rejected",
            ErrorCode::InspectionFailed => "inspection_failed",
            ErrorCode::UnsafeSource => "unsafe_source",
            ErrorCode::UnsupportedContent => "unsupported_content",
            ErrorCode::ResponseLimitExceeded => "response_limit_exceeded",
            ErrorCode::FetchFailed => "fetch_failed",
            ErrorCode::DeadlineExceeded => "deadline_exceeded",
        }
    }

    pub fn message(self) -> &'static str {
        match self {
            ErrorCode::ContentRejected => "Response content was withheld from model context.",
            ErrorCode::InspectionFailed => "Response inspection did not complete.",
            ErrorCode::UnsafeSource => "The destination is not an allowed public web address.",
            ErrorCode::UnsupportedContent => "The response media type or encoding is not supported.",
            ErrorCode::ResponseLimitExceeded => "The response exceeded a configured size limit.",
            ErrorCode::FetchFailed => "The response could not be retrieved.",
            ErrorCode::DeadlineExceeded => "The fetch did not finish within its deadline.",
        }
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        for candidate in [
            ErrorCode::ContentRejected,
            ErrorCode::InspectionFailed,
            ErrorCode::UnsafeSource,
            ErrorCode::UnsupportedContent,
            ErrorCode::ResponseLimitExceeded,
            ErrorCode::FetchFailed,
            ErrorCode::DeadlineExceeded,
        ] {
            if candidate.as_str() == value {
                return Ok(candidate);
            }
        }
        invalid(path, "is not a public failure code")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ToolError {
    pub code: ErrorCode,
}

impl ToolError {
    pub fn message(self) -> &'static str {
        self.code.message()
    }

    fn from_json(value: &Value, path: &str) -> Result<Self> {
        let object = require_object(value, path)?;
        require_exact_fields(object, &["code", "message"], &[], path)?;
        let code = ErrorCode::parse(
            require_string(&object["code"], &format!("{path}.code"), 64)?,
            &format!("{path}.code"),
        )?;
        // The message is derived, not transported. Anything else in this field
        // means something built the error from the response instead of the code.
        if require_string(&object["message"], &format!("{path}.message"), 200)? != code.message() {
            return invalid(format!("{path}.message"), "is not the fixed message for its code");
        }
        Ok(Self { code })
    }

    fn to_json(self) -> Value {
        json!({"code": self.code.as_str(), "message": self.code.message()})
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolResultEnvelope<P> {
    pub tool_version: u32,
    pub status: Status,
    pub trust: Trust,
    pub inspection: Inspection,
    pub data: Option<P>,
    pub error: Option<ToolError>,
}

impl<P: ToolPayload> ToolResultEnvelope<P> {
    /// The only way to build a success: a completed `no_match` against a named
    /// policy, with typed data.
    pub fn admitted(policy_id: impl Into<String>, data: P) -> Self {
        Self {
            tool_version: P::VERSION,
            status: Status::Ok,
            trust: Trust::ExternalUntrusted,
            inspection: Inspection::no_match(policy_id),
            data: Some(data),
            error: None,
        }
    }

    pub fn rejected(policy_id: impl Into<String>) -> Self {
        Self {
            tool_version: P::VERSION,
            status: Status::Rejected,
            trust: Trust::ExternalUntrusted,
            inspection: Inspection::matched(policy_id),
            data: None,
            error: Some(ToolError { code: ErrorCode::ContentRejected }),
        }
    }

    /// Every non-match failure. `content_rejected` is not reachable here: a
    /// rejection is a match, and a match has its own constructor.
    pub fn failed(code: ErrorCode, inspection: Inspection) -> Result<Self> {
        if code == ErrorCode::ContentRejected {
            return invalid("error.code", "describes a rule match, which is a rejection");
        }
        let envelope = Self {
            tool_version: P::VERSION,
            status: Status::Error,
            trust: Trust::ExternalUntrusted,
            inspection,
            data: None,
            error: Some(ToolError { code }),
        };
        envelope.check()?;
        Ok(envelope)
    }

    pub fn parse(document: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(document)
            .map_err(|_| ContractError::new("envelope", "is not valid JSON"))?;
        Self::from_json(&value)
    }

    pub fn from_json(value: &Value) -> Result<Self> {
        let object = require_object(value, "envelope")?;
        require_exact_fields(object, &ENVELOPE_FIELDS, &[], "envelope")?;
        if require_u32(&object["envelope_version"], "envelope.envelope_version")? != ENVELOPE_VERSION {
            return invalid("envelope.envelope_version", "is not a supported envelope version");
        }
        if require_string(&object["tool"], "envelope.tool", 64)? != P::TOOL {
            return invalid("envelope.tool", "does not identify the expected tool");
        }
        let tool_version = require_u32(&object["tool_version"], "envelope.tool_version")?;
        if tool_version != P::VERSION {
            return invalid("envelope.tool_version", "is not a supported tool version");
        }
        let status =
            Status::parse(require_string(&object["status"], "envelope.status", 32)?, "envelope.status")?;
        let trust = Trust::parse(require_string(&object["trust"], "envelope.trust", 64)?, "envelope.trust")?;
        let inspection = Inspection::from_json(&object["inspection"], "envelope.inspection")?;
        let data = match &object["data"] {
            Value::Null => None,
            other => Some(P::from_json(other)?),
        };
        let error = match &object["error"] {
            Value::Null => None,
            other => Some(ToolError::from_json(other, "envelope.error")?),
        };
        let envelope = Self { tool_version, status, trust, inspection, data, error };
        envelope.check()?;
        Ok(envelope)
    }

    /// The admission rules, in one place, applied on the way in and on the way out.
    fn check(&self) -> Result<()> {
        match self.status {
            Status::Ok => {
                if self.data.is_none() {
                    return invalid("envelope.data", "is required for a successful result");
                }
                if self.error.is_some() {
                    return invalid("envelope.error", "must be null for a successful result");
                }
                if self.inspection.status != InspectionStatus::NoMatch {
                    return invalid(
                        "envelope.inspection.status",
                        "must be a completed no_match for a successful result",
                    );
                }
                if self.inspection.policy_id.is_none() {
                    return invalid(
                        "envelope.inspection.policy_id",
                        "must name the policy that admitted the content",
                    );
                }
            }
            Status::Rejected | Status::Error => {
                if self.data.is_some() {
                    return invalid("envelope.data", "must be null when no content is released");
                }
                let Some(error) = self.error else {
                    return invalid("envelope.error", "is required when no content is released");
                };
                let rejected = error.code == ErrorCode::ContentRejected;
                if (self.status == Status::Rejected) != rejected {
                    return invalid("envelope.error.code", "does not match the envelope status");
                }
                if rejected && self.inspection.status != InspectionStatus::Match {
                    return invalid(
                        "envelope.inspection.status",
                        "must record the match that caused the rejection",
                    );
                }
            }
        }
        Ok(())
    }

    pub fn to_json(&self) -> Value {
        let mut object = Map::new();
        object.insert("envelope_version".into(), json!(ENVELOPE_VERSION));
        object.insert("tool".into(), json!(P::TOOL));
        object.insert("tool_version".into(), json!(self.tool_version));
        object.insert("status".into(), json!(self.status.as_str()));
        object.insert("trust".into(), json!(self.trust.as_str()));
        object.insert("inspection".into(), self.inspection.to_json());
        object.insert("data".into(), self.data.as_ref().map(ToolPayload::to_json).unwrap_or(Value::Null));
        object.insert("error".into(), self.error.map(ToolError::to_json).unwrap_or(Value::Null));
        Value::Object(object)
    }
}

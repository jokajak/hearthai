//! The reusable tool-result envelope, version 1.
//!
//! Webfetch is its first user, not its owner: a later consumer (research is the
//! one the plan names) declares its own [`Payload`] and reuses every control
//! field here unchanged. That is what keeps a second tool from inventing a
//! second way to say "this came from outside and was checked".

use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{ContractError, Result};

/// The only envelope version this build understands.
pub const ENVELOPE_VERSION: u32 = 1;

/// A typed tool payload carried in [`Envelope::data`].
///
/// Implementors own their own field validation; the envelope owns the control
/// fields and refuses to carry a payload under the wrong tool identity.
pub trait Payload: Serialize + DeserializeOwned {
    /// Registered tool key, e.g. `web_fetch`.
    const TOOL: &'static str;
    /// Registered tool version, e.g. `1`.
    const TOOL_VERSION: u32;

    /// Validate payload-internal invariants.
    fn validate(&self) -> Result<()>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Ok,
    Rejected,
    Error,
}

/// What the result says about the data's provenance. It describes bytes; it
/// grants nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Trust {
    ExternalUntrusted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InspectionStatus {
    /// Every required check completed and none matched.
    NoMatch,
    /// At least one enabled rule matched.
    Match,
    /// A check could not be completed: crash, timeout, partial scan, no bundle.
    Failed,
    /// The required checks never started, because the response never got that far.
    NotRun,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Inspection {
    pub status: InspectionStatus,
    /// Reference to the immutable policy that produced `status`. Null only when
    /// no policy was in force, which can never accompany a success.
    pub policy_id: Option<String>,
}

impl Inspection {
    pub fn new(status: InspectionStatus, policy_id: Option<String>) -> Self {
        Self { status, policy_id }
    }

    pub fn not_run() -> Self {
        Self::new(InspectionStatus::NotRun, None)
    }

    /// The inspection started and could not be completed. Pair this with
    /// [`ErrorCode::InspectionFailed`]; `not_run` is not a valid companion for
    /// that code, because it would claim the checks never began.
    pub fn failed(policy_id: Option<String>) -> Self {
        Self::new(InspectionStatus::Failed, policy_id)
    }
}

/// Fixed failure codes. Every one maps to a constant message, so no code path
/// can smuggle remote text, a URL, a matched string, or a worker backtrace into
/// an error the model will read.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// An enabled rule matched anywhere in the response.
    ContentRejected,
    /// The inspection could not be completed.
    InspectionFailed,
    /// A destination or redirect target was not allowed.
    UnsafeSource,
    /// Unsupported media type, charset, or malformed encoding.
    UnsupportedContent,
    /// A header, wire-byte, decoded-byte, or output limit was exceeded.
    ResponseLimitExceeded,
    /// The request could not be completed at the network level.
    FetchFailed,
    /// The whole-run deadline elapsed.
    DeadlineExceeded,
    /// The stage or result gate failed in a way that is not the response's fault.
    InternalError,
}

impl ErrorCode {
    /// The one message this code is ever allowed to carry.
    pub const fn message(self) -> &'static str {
        match self {
            Self::ContentRejected => "Response content was withheld from model context.",
            Self::InspectionFailed => {
                "Response inspection did not complete; no content was returned."
            }
            Self::UnsafeSource => "The requested destination is not allowed.",
            Self::UnsupportedContent => "The response media type or encoding is not supported.",
            Self::ResponseLimitExceeded => "The response exceeded a configured limit.",
            Self::FetchFailed => "The request to the source could not be completed.",
            Self::DeadlineExceeded => "The request did not complete within its deadline.",
            Self::InternalError => "The tool could not complete this request.",
        }
    }

    /// Inspection statuses this code may be reported with.
    ///
    /// `no_match` is absent everywhere: a completed clean inspection is only
    /// ever reported alongside returned content, so a failure can never claim
    /// the response was checked and found clean.
    fn permits(self, status: InspectionStatus) -> bool {
        match self {
            Self::ContentRejected => status == InspectionStatus::Match,
            Self::InspectionFailed => status == InspectionStatus::Failed,
            _ => matches!(status, InspectionStatus::NotRun | InspectionStatus::Failed),
        }
    }

    const fn status(self) -> Status {
        match self {
            Self::ContentRejected => Status::Rejected,
            _ => Status::Error,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolError {
    pub code: ErrorCode,
    pub message: String,
}

impl ToolError {
    /// The only constructor: the message comes from the code, never from a caller.
    pub fn new(code: ErrorCode) -> Self {
        Self {
            code,
            message: code.message().to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope<P> {
    pub envelope_version: u32,
    pub tool: String,
    pub tool_version: u32,
    pub status: Status,
    pub trust: Trust,
    pub inspection: Inspection,
    pub data: Option<P>,
    pub error: Option<ToolError>,
}

impl<P: Payload> Envelope<P> {
    /// Build a success. Callers cannot assemble one without a completed clean
    /// inspection and the policy that produced it.
    pub fn ok(data: P, policy_id: impl Into<String>) -> Result<Self> {
        let envelope = Self {
            envelope_version: ENVELOPE_VERSION,
            tool: P::TOOL.to_string(),
            tool_version: P::TOOL_VERSION,
            status: Status::Ok,
            trust: Trust::ExternalUntrusted,
            inspection: Inspection::new(InspectionStatus::NoMatch, Some(policy_id.into())),
            data: Some(data),
            error: None,
        };
        envelope.validate()?;
        Ok(envelope)
    }

    /// Build a rejection or error. The status follows from the code.
    pub fn failure(code: ErrorCode, inspection: Inspection) -> Result<Self> {
        let envelope = Self {
            envelope_version: ENVELOPE_VERSION,
            tool: P::TOOL.to_string(),
            tool_version: P::TOOL_VERSION,
            status: code.status(),
            trust: Trust::ExternalUntrusted,
            inspection,
            data: None,
            error: Some(ToolError::new(code)),
        };
        envelope.validate()?;
        Ok(envelope)
    }

    /// Parse and validate an envelope of this payload type.
    ///
    /// Consumers validate before admitting anything to model context, so this is
    /// the same gate on both sides of the wire.
    pub fn from_json(value: &str) -> Result<Self> {
        let envelope: Self = serde_json::from_str(value)
            .map_err(|error| ContractError::new(format!("envelope is not valid: {error}")))?;
        envelope.validate()?;
        Ok(envelope)
    }

    pub fn to_json(&self) -> Result<String> {
        self.validate()?;
        serde_json::to_string(self).map_err(|error| {
            ContractError::new(format!("envelope could not be serialized: {error}"))
        })
    }

    pub fn validate(&self) -> Result<()> {
        if self.envelope_version != ENVELOPE_VERSION {
            return Err(ContractError::new(format!(
                "unsupported envelope_version: {}",
                self.envelope_version
            )));
        }
        if self.tool != P::TOOL || self.tool_version != P::TOOL_VERSION {
            return Err(ContractError::new(format!(
                "envelope carries {}:v{}, expected {}:v{}",
                self.tool,
                self.tool_version,
                P::TOOL,
                P::TOOL_VERSION
            )));
        }
        match (self.status, &self.data, &self.error) {
            (Status::Ok, Some(data), None) => {
                if self.inspection.status != InspectionStatus::NoMatch {
                    return Err(ContractError::new(
                        "ok requires a completed no_match inspection",
                    ));
                }
                if self.inspection.policy_id.is_none() {
                    return Err(ContractError::new(
                        "ok requires the policy that cleared the response",
                    ));
                }
                data.validate()?;
            }
            (Status::Ok, _, _) => {
                return Err(ContractError::new(
                    "ok requires non-null data and null error",
                ));
            }
            (status, None, Some(error)) => {
                if error.code.status() != status {
                    return Err(ContractError::new(format!(
                        "{:?} is not the status for error code {:?}",
                        status, error.code
                    )));
                }
                if error.message != error.code.message() {
                    return Err(ContractError::new(
                        "error.message is not this code's fixed message",
                    ));
                }
                if !error.code.permits(self.inspection.status) {
                    return Err(ContractError::new(format!(
                        "inspection status {:?} cannot accompany error code {:?}",
                        self.inspection.status, error.code
                    )));
                }
            }
            _ => {
                return Err(ContractError::new(
                    "a failure requires null data and a fixed error",
                ));
            }
        }
        if let Some(policy_id) = &self.inspection.policy_id {
            validate_policy_id(policy_id)?;
        } else if self.inspection.status == InspectionStatus::Match {
            return Err(ContractError::new(
                "a match must name the policy that matched",
            ));
        }
        Ok(())
    }
}

/// Policy IDs are server-owned identifiers, not free text copied from anywhere.
fn validate_policy_id(value: &str) -> Result<()> {
    let shaped = !value.is_empty()
        && value.len() <= 64
        && value.chars().all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || matches!(character, '-' | '_' | '.')
        });
    if shaped {
        Ok(())
    } else {
        Err(ContractError::new(
            "inspection.policy_id is not a valid policy identifier",
        ))
    }
}

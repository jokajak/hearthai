//! The fetch stage of `web_fetch:v1`.
//!
//! This crate downloads exactly one bounded HTTP(S) response into a run-scoped
//! artifact and stops. It does not decode charsets, run detection rules,
//! convert HTML, or construct a result envelope: all of that happens in the
//! inspector, in a separate pod without the Internet access this stage needs.
//! Keeping the split at the process boundary is the point - a parser bug here
//! would inherit egress, and a parser bug there inherits nothing.

pub mod artifact;
pub mod clock;
pub mod destination;
pub mod fetch;
pub mod limits;
pub mod net;

use hearthai_web_fetch_contracts::ErrorCode;
use url::Url;

pub use destination::{DestinationPolicy, PolicyError};
pub use fetch::Fetcher;
pub use limits::FetchLimits;

/// Stage failures, each mapping onto exactly one public error code.
///
/// The variants are coarse on purpose. A finer distinction would have to be
/// explained to the caller, and the explanation is where response content would
/// start leaking out of the sandbox.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FetchError {
    /// The destination, or a redirect target, is not an allowed public address.
    UnsafeSource,
    /// The transport failed: DNS, connection, TLS, or a truncated read.
    Network,
    /// A header, redirect, or body ceiling was reached.
    ResponseLimit,
    /// The media type is outside the supported text set, or the header does not parse.
    UnsupportedContent,
    /// The stage ran out of its network time budget.
    Deadline,
    /// The artifact could not be written, or did not survive its own checks.
    Storage,
    /// No valid destination policy is configured, so nothing can be admitted.
    PolicyUnavailable,
}

impl FetchError {
    pub fn code(self) -> ErrorCode {
        match self {
            // A missing policy is indistinguishable, from the caller's side,
            // from a destination that could not be approved - because it is one.
            FetchError::UnsafeSource | FetchError::PolicyUnavailable => ErrorCode::UnsafeSource,
            FetchError::Network | FetchError::Storage => ErrorCode::FetchFailed,
            FetchError::ResponseLimit => ErrorCode::ResponseLimitExceeded,
            FetchError::UnsupportedContent => ErrorCode::UnsupportedContent,
            FetchError::Deadline => ErrorCode::DeadlineExceeded,
        }
    }
}

/// The completed download, before anything has looked inside it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FetchOutcome {
    pub final_url: Url,
    pub http_status: u16,
    pub media_type: String,
    pub charset: Option<String>,
    pub content_encoding: Option<String>,
    pub retrieved_at: String,
    pub redirects: u8,
    pub body: Vec<u8>,
}

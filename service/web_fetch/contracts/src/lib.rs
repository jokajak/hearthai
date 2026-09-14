//! Wire types for HearthAI's isolated webfetch tool.
//!
//! Two contracts live here. The first is `web_fetch:v1` itself - one URL in,
//! inspected content or a fixed error out. The second is the reusable tool
//! result envelope that carries it, defined separately so a future consumer can
//! validate and use the same admission rules without a second fetch contract.
//!
//! Nothing in this crate performs I/O. It is linked by the fetch worker, by the
//! offline inspector, and by tests that replay the shared JSON fixtures under
//! `service/web_fetch/fixtures/`, which the Python control plane replays too.

pub mod envelope;
pub mod error;
mod json;
pub mod url_policy;
pub mod web_fetch;

pub use envelope::{
    ENVELOPE_VERSION, ErrorCode, Inspection, InspectionStatus, Status, ToolError, ToolPayload,
    ToolResultEnvelope, Trust,
};
pub use error::{ContractError, Result};
pub use web_fetch::{
    ConversionMethod, MAX_CONTENT_BYTES, OutputFormat, POLICY_ID, SUPPORTED_MEDIA_TYPES, WebFetchData,
    WebFetchEnvelope, WebFetchRequest, is_html_media_type, is_supported_media_type, split_content_type,
};

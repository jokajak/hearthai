//! The `web_fetch:v1` request and result payload.

use serde::{Deserialize, Serialize};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;
use url::Url;

use crate::envelope::{Envelope, Payload};
use crate::limits::{MAX_CONTENT_CHARS, MAX_CONTENT_TYPE_CHARS, MAX_URL_CHARS};
use crate::{ContractError, Result};

pub const TOOL: &str = "web_fetch";
pub const TOOL_VERSION: u32 = 1;

/// A validated webfetch envelope.
pub type WebFetchEnvelope = Envelope<WebFetchData>;

/// Output format. It selects deterministic conversion only; no value of it
/// changes what is inspected or how a match is handled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Format {
    #[default]
    Markdown,
    Text,
    /// The decoded source, returned as inert text. It skips conversion, never
    /// scanning.
    Html,
}

/// Which converter produced the returned content. Fixed identifiers: a
/// converter reports what it is, and never returns a suggestion, a URL, or an
/// instruction to the caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConversionMethod {
    /// Native HTML cleanup and conversion.
    Native,
    /// Main-content extraction, then conversion.
    Extracted,
    /// Conversion without aggressive boilerplate removal.
    Basic,
    /// Non-HTML text returned as received after decoding.
    Passthrough,
    /// Decoded HTML source returned as inert text.
    Source,
}

/// What the model may supply: a URL, and optionally an output format.
///
/// There is deliberately nowhere to put headers, cookies, credentials, rules, a
/// timeout, a proxy, or a skip-scan flag — unknown fields are rejected outright.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WebFetchRequest {
    pub url: String,
    #[serde(default)]
    pub format: Format,
}

impl WebFetchRequest {
    pub fn new(url: impl Into<String>, format: Format) -> Self {
        Self {
            url: url.into(),
            format,
        }
    }

    pub fn from_json(value: &str) -> Result<Self> {
        let request: Self = serde_json::from_str(value)
            .map_err(|error| ContractError::new(format!("request is not valid: {error}")))?;
        request.validated_url()?;
        Ok(request)
    }

    /// Parse and check the URL, returning the exact form the fetcher will use.
    ///
    /// The fragment is dropped because it is never sent on the wire; everything
    /// else must already be acceptable rather than be repaired here.
    pub fn validated_url(&self) -> Result<Url> {
        let url = self.destination_url()?;
        check_fetchable(&url)?;
        Ok(url)
    }

    /// The same parse without the port rule, for the fetcher, which checks ports
    /// against its own destination policy on every hop including the first.
    pub fn destination_url(&self) -> Result<Url> {
        if self.url.len() > MAX_URL_CHARS {
            return Err(ContractError::new(format!(
                "request.url must be at most {MAX_URL_CHARS} characters"
            )));
        }
        let mut url = Url::parse(self.url.trim())
            .map_err(|error| ContractError::new(format!("request.url is not a URL: {error}")))?;
        check_destination(&url)?;
        url.set_fragment(None);
        Ok(url)
    }
}

/// Destination rules that hold for the requested URL and for every redirect
/// target, enforced again in the fetcher before each hop is attempted.
///
/// The port rule lives in [`check_fetchable`] rather than here because the
/// fetcher checks ports against its own destination policy, which is also what
/// decides which addresses are reachable at all.
pub fn check_destination(url: &Url) -> Result<()> {
    match url.scheme() {
        "http" | "https" => {}
        other => {
            return Err(ContractError::new(format!(
                "unsupported URL scheme: {other}"
            )));
        }
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(ContractError::new("URL userinfo is not supported"));
    }
    if url.host_str().is_none_or(str::is_empty) {
        return Err(ContractError::new("URL must have a host"));
    }
    Ok(())
}

/// [`check_destination`], plus the v1 rule that only the default web ports are
/// fetchable.
pub fn check_fetchable(url: &Url) -> Result<()> {
    check_destination(url)?;
    match url.port() {
        None | Some(80 | 443) => Ok(()),
        Some(port) => Err(ContractError::new(format!("port {port} is not supported"))),
    }
}

/// The successful payload. Every string in it except the server-owned enums and
/// `source_id` came from the response, and is inspected before release.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WebFetchData {
    /// Run-scoped opaque identifier, not a handle to anything.
    pub source_id: String,
    pub final_url: String,
    pub http_status: u16,
    pub content_type: String,
    pub retrieved_at: String,
    pub format: Format,
    pub conversion_method: ConversionMethod,
    pub content: String,
}

impl Payload for WebFetchData {
    const TOOL: &'static str = TOOL;
    const TOOL_VERSION: u32 = TOOL_VERSION;

    fn validate(&self) -> Result<()> {
        if !is_source_id(&self.source_id) {
            return Err(ContractError::new(
                "data.source_id must be a run-scoped source identifier",
            ));
        }
        if self.final_url.len() > MAX_URL_CHARS {
            return Err(ContractError::new("data.final_url is too long"));
        }
        let final_url = Url::parse(&self.final_url)
            .map_err(|error| ContractError::new(format!("data.final_url is not a URL: {error}")))?;
        // Reporting, not authorizing: the port a response actually came from is
        // the fetcher's policy decision, already made. What matters here is that
        // the reported URL is a plain HTTP(S) URL with no userinfo in it.
        check_destination(&final_url)?;
        if !(100..=599).contains(&self.http_status) {
            return Err(ContractError::new(
                "data.http_status is not an HTTP status code",
            ));
        }
        if self.content_type.is_empty() || self.content_type.len() > MAX_CONTENT_TYPE_CHARS {
            return Err(ContractError::new(
                "data.content_type is not a usable media type",
            ));
        }
        if self
            .content_type
            .chars()
            .any(|character| character.is_control() || !character.is_ascii())
        {
            return Err(ContractError::new(
                "data.content_type must be printable ASCII",
            ));
        }
        OffsetDateTime::parse(&self.retrieved_at, &Rfc3339)
            .map_err(|_| ContractError::new("data.retrieved_at must be an RFC 3339 timestamp"))?;
        if self.content.chars().count() > MAX_CONTENT_CHARS {
            return Err(ContractError::new(
                "data.content exceeds the returned-content limit",
            ));
        }
        if self.format == Format::Html && self.conversion_method != ConversionMethod::Source {
            return Err(ContractError::new(
                "html output is only ever returned as inert source",
            ));
        }
        Ok(())
    }
}

fn is_source_id(value: &str) -> bool {
    value.strip_prefix("source-").is_some_and(|suffix| {
        !suffix.is_empty() && suffix.len() <= 32 && suffix.chars().all(|c| c.is_ascii_digit())
    })
}

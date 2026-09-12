//! `web_fetch:v1`: the request the model may send and the payload it may receive.

use serde_json::{Value, json};
use url::Url;

use crate::envelope::ToolPayload;
use crate::error::{Result, invalid};
use crate::json::{require_exact_fields, require_object, require_string, require_u32};
use crate::url_policy::{MAX_URL_CHARS, parse_public_url};

pub const MAX_CONTENT_BYTES: usize = 1024 * 1024;
pub const POLICY_ID: &str = "web-content-v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum OutputFormat {
    #[default]
    Markdown,
    Text,
    Html,
}

impl OutputFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            OutputFormat::Markdown => "markdown",
            OutputFormat::Text => "text",
            OutputFormat::Html => "html",
        }
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        match value {
            "markdown" => Ok(OutputFormat::Markdown),
            "text" => Ok(OutputFormat::Text),
            "html" => Ok(OutputFormat::Html),
            _ => invalid(path, "must be markdown, text, or html"),
        }
    }
}

/// Which converter produced the content. Operator-owned and fixed: the model
/// picks a format, never a converter, and a converter never returns a URL, a
/// tool call, or an instruction in this field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConversionMethod {
    /// Native HTML-to-Markdown with content cleanup.
    Native,
    /// Main-content extraction, after native cleanup produced nothing usable.
    Extracted,
    /// Whole-body conversion without aggressive boilerplate removal.
    Basic,
    /// Non-HTML text returned as-is.
    Passthrough,
    /// Decoded HTML returned as inert source for `format: html`.
    Source,
}

impl ConversionMethod {
    pub fn as_str(self) -> &'static str {
        match self {
            ConversionMethod::Native => "native",
            ConversionMethod::Extracted => "extracted",
            ConversionMethod::Basic => "basic",
            ConversionMethod::Passthrough => "passthrough",
            ConversionMethod::Source => "source",
        }
    }

    fn parse(value: &str, path: &str) -> Result<Self> {
        match value {
            "native" => Ok(ConversionMethod::Native),
            "extracted" => Ok(ConversionMethod::Extracted),
            "basic" => Ok(ConversionMethod::Basic),
            "passthrough" => Ok(ConversionMethod::Passthrough),
            "source" => Ok(ConversionMethod::Source),
            _ => invalid(path, "is not a known conversion method"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebFetchRequest {
    pub url: Url,
    pub format: OutputFormat,
}

impl WebFetchRequest {
    pub fn parse(document: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(document)
            .map_err(|_| crate::error::ContractError::new("request", "is not valid JSON"))?;
        Self::from_json(&value)
    }

    pub fn from_json(value: &Value) -> Result<Self> {
        let object = require_object(value, "request")?;
        // Anything beyond these two keys - headers, credentials, rules, a
        // skip-scan flag, an image, a command - is a request to reconfigure the
        // worker, and is refused before anything is executed.
        require_exact_fields(object, &["url"], &["format"], "request")?;
        let url =
            parse_public_url(require_string(&object["url"], "request.url", MAX_URL_CHARS)?, "request.url")?;
        let format = match object.get("format") {
            None => OutputFormat::default(),
            Some(value) => {
                OutputFormat::parse(require_string(value, "request.format", 32)?, "request.format")?
            }
        };
        Ok(Self { url, format })
    }

    pub fn to_json(&self) -> Value {
        json!({"url": self.url.as_str(), "format": self.format.as_str()})
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebFetchData {
    /// Run-scoped and opaque. Not a path, not a handle, not something a later
    /// call can dereference into the stored response.
    pub source_id: String,
    pub final_url: String,
    pub http_status: u16,
    pub content_type: String,
    pub retrieved_at: String,
    pub format: OutputFormat,
    pub conversion_method: ConversionMethod,
    pub content: String,
}

impl ToolPayload for WebFetchData {
    const TOOL: &'static str = "web_fetch";
    const VERSION: u32 = 1;

    fn from_json(value: &Value) -> Result<Self> {
        let object = require_object(value, "data")?;
        require_exact_fields(
            object,
            &[
                "source_id",
                "final_url",
                "http_status",
                "content_type",
                "retrieved_at",
                "format",
                "conversion_method",
                "content",
            ],
            &[],
            "data",
        )?;
        let source_id = require_string(&object["source_id"], "data.source_id", 64)?;
        if !is_source_id(source_id) {
            return invalid("data.source_id", "is not a run-scoped source identifier");
        }
        // final_url is remote-influenced - redirects choose it - so it gets the
        // same admission as the requested URL and is inspected like any other
        // returned string.
        let final_url = parse_public_url(
            require_string(&object["final_url"], "data.final_url", MAX_URL_CHARS)?,
            "data.final_url",
        )?;
        let http_status = require_u32(&object["http_status"], "data.http_status")?;
        if !(100..=599).contains(&http_status) {
            return invalid("data.http_status", "is not an HTTP status code");
        }
        // The supported set, not just the syntax: the control-plane validator
        // checks the same list, and a validator that is merely stricter on one
        // side is still a place the two can disagree.
        let content_type = require_string(&object["content_type"], "data.content_type", 128)?;
        if !is_supported_media_type(content_type) {
            return invalid("data.content_type", "is not a supported media type");
        }
        let retrieved_at = require_string(&object["retrieved_at"], "data.retrieved_at", 40)?;
        if !is_rfc3339_utc(retrieved_at) {
            return invalid("data.retrieved_at", "is not an RFC 3339 UTC timestamp");
        }
        let format =
            OutputFormat::parse(require_string(&object["format"], "data.format", 32)?, "data.format")?;
        let conversion_method = ConversionMethod::parse(
            require_string(&object["conversion_method"], "data.conversion_method", 32)?,
            "data.conversion_method",
        )?;
        let content = match &object["content"] {
            Value::String(text) => text,
            _ => return invalid("data.content", "must be a string"),
        };
        if content.len() > MAX_CONTENT_BYTES {
            return invalid("data.content", "is longer than the contract allows");
        }
        Ok(Self {
            source_id: source_id.to_owned(),
            final_url: final_url.to_string(),
            http_status: http_status as u16,
            content_type: content_type.to_owned(),
            retrieved_at: retrieved_at.to_owned(),
            format,
            conversion_method,
            content: content.clone(),
        })
    }

    fn to_json(&self) -> Value {
        json!({
            "source_id": self.source_id,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "retrieved_at": self.retrieved_at,
            "format": self.format.as_str(),
            "conversion_method": self.conversion_method.as_str(),
            "content": self.content,
        })
    }
}

fn is_source_id(value: &str) -> bool {
    let Some(suffix) = value.strip_prefix("source-") else {
        return false;
    };
    (1..=32).contains(&suffix.len()) && suffix.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
}

/// `type/subtype` in lowercase, with no parameters: the charset is consumed
/// during decoding and does not travel in the result.
fn is_media_type(value: &str) -> bool {
    let Some((kind, subtype)) = value.split_once('/') else {
        return false;
    };
    let token = |part: &str| {
        !part.is_empty()
            && part
                .chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '+' | '-'))
    };
    token(kind) && token(subtype)
}

/// `YYYY-MM-DDTHH:MM:SS[.fff]Z`. UTC only, so two records of the same fetch
/// cannot disagree about ordering because of an offset.
fn is_rfc3339_utc(value: &str) -> bool {
    let Some(body) = value.strip_suffix('Z') else {
        return false;
    };
    let (date_time, fraction) = match body.split_once('.') {
        Some((head, tail)) => (head, Some(tail)),
        None => (body, None),
    };
    if let Some(fraction) = fraction
        && (fraction.is_empty() || fraction.len() > 9 || !fraction.chars().all(|c| c.is_ascii_digit()))
    {
        return false;
    }
    let Some((date, time)) = date_time.split_once('T') else {
        return false;
    };
    let digits = |part: &str, width: usize| part.len() == width && part.chars().all(|c| c.is_ascii_digit());
    let date_parts: Vec<&str> = date.split('-').collect();
    let time_parts: Vec<&str> = time.split(':').collect();
    if date_parts.len() != 3 || time_parts.len() != 3 {
        return false;
    }
    digits(date_parts[0], 4)
        && digits(date_parts[1], 2)
        && digits(date_parts[2], 2)
        && digits(time_parts[0], 2)
        && digits(time_parts[1], 2)
        && digits(time_parts[2], 2)
        && (1..=12).contains(&date_parts[1].parse::<u8>().unwrap_or(0))
        && (1..=31).contains(&date_parts[2].parse::<u8>().unwrap_or(0))
        && time_parts[0].parse::<u8>().unwrap_or(99) <= 23
        && time_parts[1].parse::<u8>().unwrap_or(99) <= 59
        && time_parts[2].parse::<u8>().unwrap_or(99) <= 60
}

/// The text media types v1 will process. Anything else - images, PDFs,
/// archives, anything that would need a binary parser or a browser engine - is
/// refused before it is stored, let alone converted.
pub const SUPPORTED_MEDIA_TYPES: [&str; 9] = [
    "text/html",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/csv",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
];

/// Splits a `content-type` header into its lowercase essence and its charset.
///
/// Returns `None` for a header that does not parse, rather than guessing: an
/// ambiguous type is an unsupported type.
pub fn split_content_type(header: &str) -> Option<(String, Option<String>)> {
    let mut parts = header.split(';');
    let essence = parts.next()?.trim().to_ascii_lowercase();
    if !is_media_type(&essence) {
        return None;
    }
    let mut charset = None;
    for parameter in parts {
        let (name, value) = parameter.split_once('=')?;
        if name.trim().eq_ignore_ascii_case("charset") {
            let value = value.trim().trim_matches('"').to_ascii_lowercase();
            if value.is_empty() {
                return None;
            }
            charset = Some(value);
        }
    }
    Some((essence, charset))
}

pub fn is_supported_media_type(essence: &str) -> bool {
    SUPPORTED_MEDIA_TYPES.contains(&essence)
}

pub fn is_html_media_type(essence: &str) -> bool {
    matches!(essence, "text/html" | "application/xhtml+xml")
}

pub type WebFetchEnvelope = crate::envelope::ToolResultEnvelope<WebFetchData>;

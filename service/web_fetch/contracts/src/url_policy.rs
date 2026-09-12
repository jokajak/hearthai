//! Syntactic URL admission shared by the request and by `final_url` in a result.
//!
//! This layer answers "is this a URL v1 is willing to talk about at all". It
//! deliberately does *not* decide whether the destination is reachable: address
//! classification needs DNS answers and the deployment's cluster ranges, and
//! lives in the fetch worker's destination policy.

use url::{Host, Url};

use crate::error::{ContractError, Result, invalid};

pub const MAX_URL_CHARS: usize = 2048;
pub const ALLOWED_PORTS: [u16; 2] = [80, 443];

pub fn parse_public_url(value: &str, path: &str) -> Result<Url> {
    if value.len() > MAX_URL_CHARS {
        return invalid(path, "is longer than the contract allows");
    }
    let parsed = Url::parse(value).map_err(|_| ContractError::new(path, "must be an absolute URL"))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return invalid(path, "must use the http or https scheme");
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return invalid(path, "must not carry userinfo");
    }
    match parsed.host() {
        Some(Host::Domain("")) => return invalid(path, "must name a host"),
        Some(_) => {}
        None => return invalid(path, "must name a host"),
    }
    let port = parsed.port_or_known_default().unwrap_or_default();
    if !ALLOWED_PORTS.contains(&port) {
        return invalid(path, "must use port 80 or 443");
    }
    Ok(parsed)
}

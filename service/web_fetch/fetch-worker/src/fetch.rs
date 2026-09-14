//! One bounded response, or nothing.
//!
//! The loop's shape is the point: resolve, validate every answer, connect to
//! the address that was validated, and do it again from scratch for each
//! redirect. Nothing is streamed to the caller while this runs, and the body is
//! not looked at here beyond counting its bytes - that is the offline stage's
//! job, in a pod that cannot reach the network this one just used.

use std::io::Read;
use std::net::IpAddr;
use std::time::{Duration, SystemTime};

use hearthai_web_fetch_contracts::url_policy::parse_public_url;
use hearthai_web_fetch_contracts::{WebFetchRequest, is_supported_media_type, split_content_type};
use url::Url;

use crate::clock::{Clock, rfc3339_utc};
use crate::destination::DestinationPolicy;
use crate::limits::FetchLimits;
use crate::net::{Connector, Resolver, is_redirect};
use crate::{FetchError, FetchOutcome};

pub struct Fetcher<'a> {
    pub policy: &'a DestinationPolicy,
    pub resolver: &'a dyn Resolver,
    pub connector: &'a dyn Connector,
    pub clock: &'a dyn Clock,
    pub limits: FetchLimits,
}

impl Fetcher<'_> {
    pub fn fetch(&self, request: &WebFetchRequest) -> Result<FetchOutcome, FetchError> {
        let started = self.clock.now();
        let mut url = request.url.clone();
        let mut redirects = 0u8;

        loop {
            let budget = self.remaining(started)?;
            let address = self.select_address(&url, budget)?;
            let response = self.connector.get(&url, address, budget)?;

            if response.header_bytes > self.limits.max_header_bytes {
                return Err(FetchError::ResponseLimit);
            }

            if is_redirect(response.status)
                && let Some(location) = response.location.as_deref()
            {
                if redirects == self.limits.max_redirects {
                    return Err(FetchError::ResponseLimit);
                }
                redirects += 1;
                // Resolved against the current hop, then admitted from scratch:
                // a redirect is a new destination, not a continuation of one
                // that was already approved.
                let target = url.join(location).map_err(|_| FetchError::UnsafeSource)?;
                url = parse_public_url(target.as_str(), "redirect").map_err(|_| FetchError::UnsafeSource)?;
                continue;
            }

            let header = response.content_type.clone().ok_or(FetchError::UnsupportedContent)?;
            let (essence, charset) = split_content_type(&header).ok_or(FetchError::UnsupportedContent)?;
            if !is_supported_media_type(&essence) {
                return Err(FetchError::UnsupportedContent);
            }

            let body = self.read_bounded(response.body)?;
            self.remaining(started)?;
            return Ok(FetchOutcome {
                final_url: url,
                http_status: response.status,
                media_type: essence,
                charset,
                content_encoding: response.content_encoding,
                retrieved_at: rfc3339_utc(self.clock.now()),
                redirects,
                body,
            });
        }
    }

    fn remaining(&self, started: SystemTime) -> Result<Duration, FetchError> {
        self.limits
            .network_time
            .checked_sub(self.clock.elapsed_since(started))
            .filter(|left| !left.is_zero())
            .ok_or(FetchError::Deadline)
    }

    /// Every answer for the name must be permitted, and the one address that is
    /// handed to the connector is one of the answers that was just checked.
    fn select_address(&self, url: &Url, budget: Duration) -> Result<IpAddr, FetchError> {
        let host = url.host_str().ok_or(FetchError::UnsafeSource)?;
        let addresses = match host.trim_matches(['[', ']']).parse::<IpAddr>() {
            Ok(literal) => vec![literal],
            Err(_) => self.resolver.resolve(host, budget)?,
        };
        if !self.policy.permits_all(&addresses) {
            return Err(FetchError::UnsafeSource);
        }
        addresses.first().copied().ok_or(FetchError::UnsafeSource)
    }

    /// Reads one byte past the ceiling so an oversized body is detected rather
    /// than silently truncated into a scanned prefix.
    fn read_bounded(&self, body: Box<dyn Read>) -> Result<Vec<u8>, FetchError> {
        let ceiling = self.limits.max_wire_body_bytes;
        let mut buffer = Vec::new();
        body.take(ceiling as u64 + 1).read_to_end(&mut buffer).map_err(|_| FetchError::Network)?;
        if buffer.len() > ceiling {
            return Err(FetchError::ResponseLimit);
        }
        Ok(buffer)
    }
}

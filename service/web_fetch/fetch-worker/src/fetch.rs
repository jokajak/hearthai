//! One bounded HTTP(S) GET, written to a run-scoped artifact.

use std::sync::Arc;
use std::time::{Duration, Instant};

use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;
use ureq::Agent;
use ureq::config::Config;
use ureq::http::Response;
use ureq::unversioned::transport::DefaultConnector;
use ureq::{Body, Error as UreqError};
use url::Url;

use hearthai_webfetch_contracts::artifact::{FetchArtifact, sha256_hex};
use hearthai_webfetch_contracts::envelope::ErrorCode;
use hearthai_webfetch_contracts::limits::{
    FETCH_DEADLINE_SECONDS, MAX_HEADER_BYTES, MAX_REDIRECTS, MAX_WIRE_BYTES,
};
use hearthai_webfetch_contracts::media::{MediaType, is_supported_content_encoding};
use hearthai_webfetch_contracts::web_fetch::{WebFetchRequest, check_destination};

use crate::policy::DestinationPolicy;
use crate::resolve::{HostResolver, PinnedResolver, resolve_and_validate};

/// Identifies the fetcher to servers. Fixed: a caller cannot influence what this
/// deployment says about itself.
const USER_AGENT: &str = "HearthAI-webfetch/1 (isolated fetch; no scripting)";
/// Media types v1 can do something with, in the order we prefer them.
const ACCEPT: &str = "text/html,application/xhtml+xml,text/plain;q=0.9,text/markdown;q=0.9,\
application/json;q=0.8,application/xml;q=0.7,text/csv;q=0.7,*/*;q=0.1";
/// Bodies are stored as received; the inspector decodes under its own limits.
const ACCEPT_ENCODING: &str = "identity, gzip";

pub struct Fetcher {
    policy: Arc<DestinationPolicy>,
    resolver: Arc<dyn HostResolver>,
    deadline: Duration,
}

/// What a completed fetch hands to the next stage.
#[derive(Debug)]
pub struct Fetched {
    pub artifact: FetchArtifact,
    pub body: Vec<u8>,
}

impl Fetcher {
    pub fn new(policy: Arc<DestinationPolicy>, resolver: Arc<dyn HostResolver>) -> Self {
        Self {
            policy,
            resolver,
            deadline: Duration::from_secs(FETCH_DEADLINE_SECONDS),
        }
    }

    pub fn with_deadline(mut self, deadline: Duration) -> Self {
        self.deadline = deadline;
        self
    }

    /// Fetch one URL, following redirects by hand so every hop is re-validated.
    pub fn fetch(&self, run_id: &str, request: &WebFetchRequest) -> Result<Fetched, ErrorCode> {
        let started = Instant::now();
        let requested = request
            .destination_url()
            .map_err(|_| ErrorCode::UnsafeSource)?;
        let mut current = requested.clone();
        let mut header_bytes = 0usize;
        let mut redirects = 0u32;

        loop {
            let remaining = self
                .deadline
                .checked_sub(started.elapsed())
                .filter(|left| !left.is_zero())
                .ok_or(ErrorCode::DeadlineExceeded)?;

            // Re-checked on every hop, not only on what the caller asked for.
            check_destination(&current).map_err(|_| ErrorCode::UnsafeSource)?;
            if !current
                .port_or_known_default()
                .is_some_and(|port| self.policy.permits_port(port))
            {
                return Err(ErrorCode::UnsafeSource);
            }
            let addresses = resolve_and_validate(self.resolver.as_ref(), &self.policy, &current)?;

            let agent = build_agent(addresses, remaining);
            let response = agent
                .get(current.as_str())
                .header("user-agent", USER_AGENT)
                .header("accept", ACCEPT)
                .header("accept-encoding", ACCEPT_ENCODING)
                .call()
                .map_err(classify_transport_error)?;

            header_bytes += measure_headers(&response);
            if header_bytes > MAX_HEADER_BYTES {
                return Err(ErrorCode::ResponseLimitExceeded);
            }

            if let Some(location) = redirect_target(&response) {
                redirects += 1;
                if redirects > MAX_REDIRECTS {
                    return Err(ErrorCode::ResponseLimitExceeded);
                }
                // Resolve relative targets against the hop that sent them, then
                // let the top of the loop judge the result like any other URL.
                current = current
                    .join(&location)
                    .map_err(|_| ErrorCode::UnsafeSource)?;
                continue;
            }

            return self.complete(run_id, request, &requested, &current, redirects, response);
        }
    }

    fn complete(
        &self,
        run_id: &str,
        request: &WebFetchRequest,
        requested: &Url,
        final_url: &Url,
        redirects: u32,
        response: Response<Body>,
    ) -> Result<Fetched, ErrorCode> {
        let status = response.status().as_u16();
        let header = |name: &str| {
            response
                .headers()
                .get(name)
                .and_then(|value| value.to_str().ok())
                .map(str::to_string)
        };

        // Decided before the body is read: there is no reason to store bytes
        // this pipeline will refuse to decode.
        let content_type = header("content-type").ok_or(ErrorCode::UnsupportedContent)?;
        let media = MediaType::parse(&content_type).map_err(|_| ErrorCode::UnsupportedContent)?;
        if !media.is_supported() {
            return Err(ErrorCode::UnsupportedContent);
        }
        let content_encoding = header("content-encoding");
        if !content_encoding
            .as_deref()
            .is_none_or(is_supported_content_encoding)
        {
            return Err(ErrorCode::UnsupportedContent);
        }

        let body = response
            .into_body()
            .with_config()
            .limit(MAX_WIRE_BYTES as u64)
            .read_to_vec()
            .map_err(classify_transport_error)?;

        let retrieved_at = OffsetDateTime::now_utc()
            .format(&Rfc3339)
            .map_err(|_| ErrorCode::InternalError)?;

        let artifact = FetchArtifact {
            artifact_version: hearthai_webfetch_contracts::artifact::ARTIFACT_VERSION,
            run_id: run_id.to_string(),
            source_id: "source-1".to_string(),
            requested_url_sha256: sha256_hex(requested.as_str().as_bytes()),
            final_url: final_url.to_string(),
            http_status: status,
            content_type: Some(media.header_value()),
            content_encoding,
            redirects,
            retrieved_at,
            format: request.format,
            body_bytes: body.len(),
            body_sha256: sha256_hex(&body),
            policy_profile: self.policy.profile().as_str().to_string(),
        };
        Ok(Fetched { artifact, body })
    }
}

fn build_agent(addresses: Vec<std::net::SocketAddr>, remaining: Duration) -> Agent {
    let config = Config::builder()
        // Redirects are followed by the caller of this function, one validated
        // hop at a time.
        .max_redirects(0)
        .save_redirect_history(false)
        // A 4xx or 5xx is a response with a body this tool may still return, not
        // a client error to raise.
        .http_status_as_error(false)
        // No ambient proxy, whatever the environment says.
        .proxy(None)
        .timeout_global(Some(remaining))
        .max_response_header_size(MAX_HEADER_BYTES)
        .build();
    Agent::with_parts(
        config,
        DefaultConnector::new(),
        PinnedResolver::new(addresses),
    )
}

fn measure_headers(response: &Response<Body>) -> usize {
    response
        .headers()
        .iter()
        .map(|(name, value)| name.as_str().len() + value.as_bytes().len() + 4)
        .sum()
}

fn redirect_target(response: &Response<Body>) -> Option<String> {
    if !matches!(response.status().as_u16(), 301 | 302 | 303 | 307 | 308) {
        return None;
    }
    response
        .headers()
        .get("location")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string)
}

/// Map a transport failure to a public code without carrying its text anywhere.
fn classify_transport_error(error: UreqError) -> ErrorCode {
    match error {
        UreqError::Timeout(_) | UreqError::BodyStalled => ErrorCode::DeadlineExceeded,
        UreqError::BodyExceedsLimit(_) | UreqError::LargeResponseHeader(_, _) => {
            ErrorCode::ResponseLimitExceeded
        }
        UreqError::TooManyRedirects => ErrorCode::ResponseLimitExceeded,
        UreqError::RequireHttpsOnly(_) | UreqError::BadUri(_) | UreqError::TlsRequired => {
            ErrorCode::UnsafeSource
        }
        _ => ErrorCode::FetchFailed,
    }
}

#[cfg(test)]
mod tests {
    use std::net::IpAddr;

    use hearthai_webfetch_contracts::web_fetch::Format;

    use super::*;
    use crate::policy::Profile;
    use crate::resolve::StaticResolver;
    use crate::testserver::{Reply, TestServer};

    const HOST: &str = "fixture.test";
    const PAGE: &[u8] =
        b"<html><head><title>Fixture</title></head><body><h1>Fixture</h1></body></html>";

    fn loopback() -> IpAddr {
        "127.0.0.1".parse().expect("loopback")
    }

    /// A fetcher that can reach the fixture server and nothing else special.
    fn fetcher(resolver: StaticResolver) -> Fetcher {
        Fetcher::new(
            Arc::new(DestinationPolicy::for_tests(Profile::Test)),
            Arc::new(resolver),
        )
    }

    fn request(port: u16, path: &str) -> WebFetchRequest {
        WebFetchRequest::new(format!("http://{HOST}:{port}{path}"), Format::Markdown)
    }

    #[test]
    fn a_successful_fetch_binds_the_body_to_its_description() {
        let server = TestServer::start(|path| match path {
            "/page" => Some(Reply::Body(200, "text/html; charset=utf-8", PAGE.to_vec())),
            _ => None,
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        let fetched = fetcher(resolver)
            .fetch("run-1", &request(server.port(), "/page"))
            .expect("fetched");

        assert_eq!(fetched.artifact.http_status, 200);
        assert_eq!(
            fetched.artifact.content_type.as_deref(),
            Some("text/html; charset=utf-8")
        );
        assert_eq!(fetched.artifact.body_bytes, PAGE.len());
        assert_eq!(fetched.artifact.body_sha256, sha256_hex(PAGE));
        assert_eq!(fetched.artifact.redirects, 0);
        assert_eq!(fetched.artifact.source_id, "source-1");
        // The audit-safe digest is kept; the URL itself is not in the artifact's
        // request field at all.
        assert_eq!(fetched.artifact.requested_url_sha256.len(), 64);
    }

    #[test]
    fn an_error_status_with_a_text_body_is_still_a_fetch() {
        let server =
            TestServer::start(|_| Some(Reply::Body(503, "text/plain", b"maintenance".to_vec())));
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        let fetched = fetcher(resolver)
            .fetch("run-1", &request(server.port(), "/down"))
            .expect("fetched");
        assert_eq!(fetched.artifact.http_status, 503);
        assert_eq!(fetched.body, b"maintenance");
    }

    #[test]
    fn redirects_are_followed_up_to_the_limit_and_then_refused() {
        let server = TestServer::start(|path| match path {
            "/one" => Some(Reply::Redirect(302, "/two".into())),
            "/two" => Some(Reply::Redirect(302, "/three".into())),
            "/three" => Some(Reply::Body(200, "text/html", PAGE.to_vec())),
            // A loop that always redirects, to exhaust the budget.
            "/loop" => Some(Reply::Redirect(302, "/loop".into())),
            _ => None,
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        let fetched = fetcher(resolver)
            .fetch("run-1", &request(server.port(), "/one"))
            .expect("fetched");
        assert_eq!(fetched.artifact.redirects, 2);
        assert!(fetched.artifact.final_url.ends_with("/three"));

        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        assert_eq!(
            fetcher(resolver)
                .fetch("run-1", &request(server.port(), "/loop"))
                .err(),
            Some(ErrorCode::ResponseLimitExceeded)
        );
    }

    #[test]
    fn a_redirect_into_private_space_is_refused() {
        let server = TestServer::start(|path| match path {
            "/away" => Some(Reply::Redirect(
                302,
                "http://metadata.test/latest/meta-data/".into(),
            )),
            _ => None,
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]).with(
            "metadata.test",
            &["169.254.169.254".parse().expect("address")],
        );
        assert_eq!(
            fetcher(resolver)
                .fetch("run-1", &request(server.port(), "/away"))
                .err(),
            Some(ErrorCode::UnsafeSource)
        );
    }

    #[test]
    fn a_redirect_to_another_scheme_or_port_is_refused() {
        let server = TestServer::start(|path| match path {
            "/file" => Some(Reply::Redirect(302, "file:///etc/passwd".into())),
            "/port" => Some(Reply::Redirect(302, "http://fixture.test:8443/page".into())),
            _ => None,
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        assert_eq!(
            fetcher(resolver)
                .fetch("run-1", &request(server.port(), "/file"))
                .err(),
            Some(ErrorCode::UnsafeSource)
        );

        // The port rule is the production policy's, so check it under that policy.
        let server_port = server.port();
        let production = Fetcher::new(
            Arc::new(DestinationPolicy::for_tests(Profile::Production)),
            Arc::new(StaticResolver::default().with(HOST, &[loopback()])),
        );
        assert_eq!(
            production
                .fetch("run-1", &request(server_port, "/port"))
                .err(),
            Some(ErrorCode::UnsafeSource)
        );
    }

    #[test]
    fn a_name_that_answers_differently_on_the_next_hop_is_caught_on_that_hop() {
        // The second lookup returns cluster space. Pinning the first answer to
        // the first connection does not make the second hop safe, so the second
        // hop is validated on its own.
        let server = TestServer::start(|path| match path {
            "/rebind" => Some(Reply::Redirect(302, "http://rebound.test/secrets".into())),
            _ => None,
        });
        let resolver = StaticResolver::default()
            .with(HOST, &[loopback()])
            .with("rebound.test", &["10.42.0.7".parse().expect("address")]);
        assert_eq!(
            fetcher(resolver)
                .fetch("run-1", &request(server.port(), "/rebind"))
                .err(),
            Some(ErrorCode::UnsafeSource)
        );
    }

    #[test]
    fn an_oversized_body_is_refused_rather_than_truncated() {
        let big = vec![b'a'; MAX_WIRE_BYTES + 4096];
        let server = TestServer::start(move |_| Some(Reply::Body(200, "text/plain", big.clone())));
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        assert_eq!(
            fetcher(resolver)
                .fetch("run-1", &request(server.port(), "/big"))
                .err(),
            Some(ErrorCode::ResponseLimitExceeded)
        );
    }

    #[test]
    fn unsupported_or_missing_media_types_are_refused_before_the_body_is_stored() {
        let server = TestServer::start(|path| match path {
            "/image" => Some(Reply::Body(200, "image/png", vec![0x89, b'P', b'N', b'G'])),
            "/none" => Some(Reply::Untyped(b"anonymous bytes".to_vec())),
            "/charset" => Some(Reply::Body(
                200,
                "text/html; charset=definitely-not-a-charset",
                PAGE.to_vec(),
            )),
            _ => None,
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        for path in ["/image", "/none", "/charset"] {
            let resolver = StaticResolver::default().with(HOST, &[loopback()]);
            assert_eq!(
                fetcher(resolver)
                    .fetch("run-1", &request(server.port(), path))
                    .err(),
                Some(ErrorCode::UnsupportedContent),
                "{path} should be refused"
            );
        }
        drop(resolver);
    }

    #[test]
    fn a_stalled_response_ends_at_the_deadline() {
        let server = TestServer::start(|_| Some(Reply::Stalled("text/plain")));
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        let fetcher = fetcher(resolver).with_deadline(Duration::from_millis(400));
        assert_eq!(
            fetcher
                .fetch("run-1", &request(server.port(), "/slow"))
                .err(),
            Some(ErrorCode::DeadlineExceeded)
        );
    }

    #[test]
    fn a_compressed_body_is_stored_as_received() {
        // Nothing decompresses in this stage, so the expansion limit is the
        // inspector's to enforce on bytes it can account for.
        let payload = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03".to_vec();
        let expected = payload.clone();
        let server = TestServer::start(move |_| {
            Some(Reply::Encoded(
                200,
                "text/html",
                payload.clone(),
                "content-encoding: gzip",
            ))
        });
        let resolver = StaticResolver::default().with(HOST, &[loopback()]);
        let fetched = fetcher(resolver)
            .fetch("run-1", &request(server.port(), "/gz"))
            .expect("fetched");
        assert_eq!(fetched.body, expected);
        assert_eq!(fetched.artifact.content_encoding.as_deref(), Some("gzip"));
    }

    #[test]
    fn a_refused_destination_is_never_contacted() {
        let server = TestServer::start(|_| Some(Reply::Body(200, "text/html", PAGE.to_vec())));
        // The name resolves to the fixture server, but the policy refuses
        // loopback outside a test profile.
        let production = Fetcher::new(
            Arc::new(DestinationPolicy::for_tests(Profile::Production)),
            Arc::new(StaticResolver::default().with(HOST, &[loopback()])),
        );
        assert_eq!(
            production
                .fetch("run-1", &request(server.port(), "/page"))
                .err(),
            Some(ErrorCode::UnsafeSource)
        );
        assert_eq!(
            server.requests(),
            0,
            "a refused destination received a connection"
        );
    }
}

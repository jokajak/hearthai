//! The two pieces of the network that tests replace: name resolution and the
//! single pinned request.
//!
//! Splitting them this way is what makes redirect, rebinding and limit
//! behaviour testable without a network, and it is also what lets the fetch
//! loop guarantee that the address it validated is the address the socket
//! connects to. A client that resolves the hostname a second time inside its
//! own connect would undo the check.

use std::io::Read;
use std::net::IpAddr;
use std::time::Duration;

use url::Url;

use crate::FetchError;

/// Sent on every request. The model supplies none of this.
pub const USER_AGENT: &str = "HearthAI-webfetch/1 (+isolated; no scripts)";
pub const ACCEPT: &str =
    "text/html;q=1.0, text/plain;q=0.9, application/xhtml+xml;q=0.9, text/*;q=0.6, application/json;q=0.5";

pub trait Resolver: Send + Sync {
    fn resolve(&self, host: &str, budget: Duration) -> Result<Vec<IpAddr>, FetchError>;
}

/// One HTTP exchange. Redirects are not followed here: the fetch loop has to
/// re-validate the next hop before anything connects to it.
pub trait Connector: Send + Sync {
    fn get(&self, url: &Url, address: IpAddr, budget: Duration) -> Result<HopResponse, FetchError>;
}

pub struct HopResponse {
    pub status: u16,
    pub header_bytes: usize,
    pub content_type: Option<String>,
    pub content_encoding: Option<String>,
    pub location: Option<String>,
    /// Unread. The fetch loop does the bounded read so the ceiling is enforced
    /// in one place rather than trusted to each connector.
    pub body: Box<dyn Read>,
}

pub fn is_redirect(status: u16) -> bool {
    matches!(status, 301 | 302 | 303 | 307 | 308)
}

#[cfg(feature = "network")]
pub use live::{PinnedHttpsConnector, SystemResolver};

#[cfg(feature = "network")]
mod live {
    use std::io::Read;
    use std::net::{IpAddr, SocketAddr, ToSocketAddrs};
    use std::time::Duration;

    use ureq::Agent;
    use ureq::config::Config;
    use ureq::unversioned::resolver::{ResolvedSocketAddrs, Resolver as UreqResolver};
    use ureq::unversioned::transport::NextTimeout;
    use url::Url;

    use super::{ACCEPT, Connector, HopResponse, Resolver, USER_AGENT};
    use crate::FetchError;

    #[derive(Debug, Default)]
    pub struct SystemResolver;

    impl Resolver for SystemResolver {
        fn resolve(&self, host: &str, _budget: Duration) -> Result<Vec<IpAddr>, FetchError> {
            // Port 0: the fetch loop cares about addresses, and the port comes
            // from the URL when the connection is actually made.
            let answers = (host, 0u16).to_socket_addrs().map_err(|_| FetchError::Network)?;
            let addresses: Vec<IpAddr> = answers.map(|answer| answer.ip()).collect();
            if addresses.is_empty() {
                return Err(FetchError::Network);
            }
            Ok(addresses)
        }
    }

    /// A ureq resolver that answers with exactly one already-validated address.
    ///
    /// The hostname stays in the URI, so SNI and certificate verification still
    /// see the real name; only the address selection is taken away.
    #[derive(Debug)]
    struct PinnedResolver(SocketAddr);

    impl UreqResolver for PinnedResolver {
        fn resolve(
            &self,
            _uri: &ureq::http::Uri,
            _config: &Config,
            _timeout: NextTimeout,
        ) -> Result<ResolvedSocketAddrs, ureq::Error> {
            let mut addresses = self.empty();
            addresses.push(self.0);
            Ok(addresses)
        }
    }

    #[derive(Debug, Default)]
    pub struct PinnedHttpsConnector;

    impl Connector for PinnedHttpsConnector {
        fn get(&self, url: &Url, address: IpAddr, budget: Duration) -> Result<HopResponse, FetchError> {
            let port = url.port_or_known_default().ok_or(FetchError::Network)?;
            let config = Config::builder()
                // Redirects are this crate's job; ureq must hand the 3xx back.
                .max_redirects(0)
                .max_redirects_will_error(false)
                // No ambient proxy: the environment does not get to interpose a
                // destination the policy never saw.
                .proxy(None)
                .timeout_global(Some(budget))
                .user_agent(USER_AGENT)
                .build();
            let agent = Agent::with_parts(
                config,
                ureq::unversioned::transport::DefaultConnector::new(),
                PinnedResolver(SocketAddr::new(address, port)),
            );
            let response = agent
                .get(url.as_str())
                .header("accept", ACCEPT)
                // Identity is not requested: content-encoding is undone in the
                // offline stage, under the decoded-byte ceiling.
                .header("accept-encoding", "gzip, deflate, identity")
                .call()
                .map_err(|_| FetchError::Network)?;

            let status = response.status().as_u16();
            let header_bytes =
                response.headers().iter().map(|(name, value)| name.as_str().len() + value.len() + 4).sum();
            let header = |name: &str| {
                response.headers().get(name).and_then(|value| value.to_str().ok()).map(str::to_owned)
            };
            let content_type = header("content-type");
            let content_encoding = header("content-encoding");
            let location = header("location");
            let body: Box<dyn Read> = Box::new(response.into_body().into_reader());
            Ok(HopResponse { status, header_bytes, content_type, content_encoding, location, body })
        }
    }
}

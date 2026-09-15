//! Name resolution, validated once and then pinned to the connection.
//!
//! The rebinding hole this closes: look up a name, decide the answer is
//! acceptable, then hand the *name* to an HTTP client that looks it up again and
//! connects to whatever comes back the second time. Here the lookup happens
//! once, in [`resolve_and_validate`], and the client is given a resolver that
//! can only return those exact addresses.

use std::collections::HashMap;
use std::fmt::Debug;
use std::io;
use std::net::{IpAddr, SocketAddr, ToSocketAddrs};
use std::sync::Arc;

use ureq::Error as UreqError;
use ureq::config::Config;
use ureq::http::Uri;
use ureq::unversioned::resolver::{ResolvedSocketAddrs, Resolver};
use ureq::unversioned::transport::NextTimeout;
use url::Url;

use hearthai_webfetch_contracts::envelope::ErrorCode;

use crate::policy::DestinationPolicy;

/// A name lookup. Tests substitute their own answers, including the answers a
/// hostile resolver would give.
pub trait HostResolver: Debug + Send + Sync + 'static {
    fn lookup(&self, host: &str, port: u16) -> io::Result<Vec<IpAddr>>;
}

#[derive(Debug, Default)]
pub struct SystemResolver;

impl HostResolver for SystemResolver {
    fn lookup(&self, host: &str, port: u16) -> io::Result<Vec<IpAddr>> {
        Ok((host, port)
            .to_socket_addrs()?
            .map(|address| address.ip())
            .collect())
    }
}

/// A fixed answer table, for tests that need a specific DNS answer.
#[derive(Debug, Default)]
pub struct StaticResolver {
    answers: HashMap<String, Vec<IpAddr>>,
}

impl StaticResolver {
    pub fn with(mut self, host: &str, addresses: &[IpAddr]) -> Self {
        self.answers
            .insert(host.to_ascii_lowercase(), addresses.to_vec());
        self
    }
}

impl HostResolver for StaticResolver {
    fn lookup(&self, host: &str, _port: u16) -> io::Result<Vec<IpAddr>> {
        self.answers
            .get(&host.to_ascii_lowercase())
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no answer"))
    }
}

/// Resolve one hop and decide whether it may be connected to.
///
/// Every answer must be permitted, not just the one that would be used. A name
/// that resolves to a public address *and* a cluster address is refused: which
/// one a connection would land on is not something this stage should be racing.
pub fn resolve_and_validate(
    resolver: &dyn HostResolver,
    policy: &DestinationPolicy,
    url: &Url,
) -> Result<Vec<SocketAddr>, ErrorCode> {
    let host = url.host_str().ok_or(ErrorCode::UnsafeSource)?;
    let port = url.port_or_known_default().ok_or(ErrorCode::UnsafeSource)?;
    let addresses = resolver
        .lookup(host, port)
        .map_err(|_| ErrorCode::FetchFailed)?;
    if addresses.is_empty() {
        return Err(ErrorCode::FetchFailed);
    }
    if !addresses.iter().all(|address| policy.permits(*address)) {
        return Err(ErrorCode::UnsafeSource);
    }
    Ok(addresses
        .into_iter()
        .map(|address| SocketAddr::new(address, port))
        .collect())
}

/// A resolver that answers with addresses already validated for this hop.
///
/// It ignores the URI it is handed, which is the point: the HTTP client cannot
/// reach a destination this stage did not approve, whatever DNS says next.
#[derive(Debug)]
pub struct PinnedResolver {
    addresses: Arc<Vec<SocketAddr>>,
}

impl PinnedResolver {
    pub fn new(addresses: Vec<SocketAddr>) -> Self {
        Self {
            addresses: Arc::new(addresses),
        }
    }
}

impl Resolver for PinnedResolver {
    fn resolve(
        &self,
        _uri: &Uri,
        _config: &Config,
        _timeout: NextTimeout,
    ) -> Result<ResolvedSocketAddrs, UreqError> {
        // ureq keeps at most 16 addresses per resolution.
        let mut resolved = self.empty();
        for address in self.addresses.iter().take(16) {
            resolved.push(*address);
        }
        if resolved.is_empty() {
            return Err(UreqError::HostNotFound);
        }
        Ok(resolved)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::policy::Profile;

    fn address(value: &str) -> IpAddr {
        value.parse().expect("test address")
    }

    fn url(value: &str) -> Url {
        Url::parse(value).expect("test url")
    }

    #[test]
    fn a_public_answer_is_accepted() {
        let resolver = StaticResolver::default().with("example.com", &[address("93.184.216.34")]);
        let policy = DestinationPolicy::for_tests(Profile::Production);
        let resolved = resolve_and_validate(&resolver, &policy, &url("https://example.com/page"))
            .expect("permitted");
        assert_eq!(
            resolved,
            vec![SocketAddr::new(address("93.184.216.34"), 443)]
        );
    }

    #[test]
    fn a_name_pointing_at_the_metadata_endpoint_is_refused() {
        let resolver = StaticResolver::default().with("rebind.test", &[address("169.254.169.254")]);
        let policy = DestinationPolicy::for_tests(Profile::Production);
        assert_eq!(
            resolve_and_validate(
                &resolver,
                &policy,
                &url("http://rebind.test/latest/meta-data/")
            ),
            Err(ErrorCode::UnsafeSource)
        );
    }

    #[test]
    fn a_mixed_public_and_private_answer_is_refused_entirely() {
        let resolver = StaticResolver::default().with(
            "mixed.test",
            &[address("93.184.216.34"), address("10.42.0.7")],
        );
        let policy = DestinationPolicy::for_tests(Profile::Production);
        assert_eq!(
            resolve_and_validate(&resolver, &policy, &url("https://mixed.test/")),
            Err(ErrorCode::UnsafeSource)
        );
    }

    #[test]
    fn an_empty_or_failed_lookup_is_a_fetch_failure_not_a_policy_decision() {
        let policy = DestinationPolicy::for_tests(Profile::Production);
        let resolver = StaticResolver::default();
        assert_eq!(
            resolve_and_validate(&resolver, &policy, &url("https://absent.test/")),
            Err(ErrorCode::FetchFailed)
        );
        let empty = StaticResolver::default().with("empty.test", &[]);
        assert_eq!(
            resolve_and_validate(&empty, &policy, &url("https://empty.test/")),
            Err(ErrorCode::FetchFailed)
        );
    }
}

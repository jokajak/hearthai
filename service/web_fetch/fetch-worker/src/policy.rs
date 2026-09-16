//! Destination policy: which addresses this deployment may talk to.
//!
//! Two layers, both enforced on every hop:
//!
//! * Built-in refusal of address space that is never a public web destination -
//!   loopback, private, link-local (which is where cloud metadata lives),
//!   multicast, reserved, and their IPv6 spellings including v4-mapped forms.
//! * Operator-supplied ranges for this cluster's own services and nodes, which
//!   the built-in list cannot know.
//!
//! Absent, empty, or malformed configuration is not "no restrictions": it makes
//! the capability unavailable.

use std::fs;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::path::Path;

use serde::Deserialize;

use hearthai_webfetch_contracts::{ContractError, Result};

/// Which environment the policy was written for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Profile {
    /// The only profile a deployment uses. Non-public destinations are refused.
    Production,
    /// Test harnesses only: permits loopback so a fixture server on this host is
    /// reachable. The chart never ships this, and the fetcher says so on stderr.
    Test,
}

impl Profile {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Production => "production",
            Self::Test => "test",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cidr {
    network: IpAddr,
    prefix: u8,
}

impl Cidr {
    pub fn parse(value: &str) -> Result<Self> {
        let (address, prefix) = value.split_once('/').ok_or_else(|| {
            ContractError::new(format!("denied range is not CIDR notation: {value}"))
        })?;
        let network: IpAddr = address.parse().map_err(|_| {
            ContractError::new(format!("denied range has no valid address: {value}"))
        })?;
        let prefix: u8 = prefix.parse().map_err(|_| {
            ContractError::new(format!("denied range has no valid prefix: {value}"))
        })?;
        let width = if network.is_ipv4() { 32 } else { 128 };
        if prefix > width {
            return Err(ContractError::new(format!(
                "denied range prefix is out of bounds: {value}"
            )));
        }
        Ok(Self { network, prefix })
    }

    pub fn contains(&self, address: IpAddr) -> bool {
        match (self.network, canonical(address)) {
            (IpAddr::V4(network), IpAddr::V4(candidate)) => {
                matches_prefix(&network.octets(), &candidate.octets(), self.prefix)
            }
            (IpAddr::V6(network), IpAddr::V6(candidate)) => {
                matches_prefix(&network.octets(), &candidate.octets(), self.prefix)
            }
            _ => false,
        }
    }
}

fn matches_prefix(network: &[u8], candidate: &[u8], prefix: u8) -> bool {
    let full = usize::from(prefix / 8);
    let remainder = prefix % 8;
    if network[..full] != candidate[..full] {
        return false;
    }
    if remainder == 0 {
        return true;
    }
    let mask = 0xffu8 << (8 - remainder);
    network[full] & mask == candidate[full] & mask
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyFile {
    policy_version: u32,
    profile: Profile,
    /// Cluster, service and node ranges this deployment must never reach.
    denied_cidrs: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct DestinationPolicy {
    profile: Profile,
    denied: Vec<Cidr>,
}

impl DestinationPolicy {
    /// Load and validate a policy file. Every failure here leaves the capability
    /// unavailable rather than running with a weaker policy.
    pub fn load(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path).map_err(|error| {
            ContractError::new(format!("destination policy is unreadable: {error}"))
        })?;
        let file: PolicyFile = serde_json::from_str(&raw).map_err(|error| {
            ContractError::new(format!("destination policy is not valid: {error}"))
        })?;
        if file.policy_version != 1 {
            return Err(ContractError::new("unsupported destination policy version"));
        }
        if file.denied_cidrs.is_empty() {
            return Err(ContractError::new(
                "destination policy lists no cluster ranges; refusing to run without them",
            ));
        }
        let denied = file
            .denied_cidrs
            .iter()
            .map(|value| Cidr::parse(value))
            .collect::<Result<Vec<_>>>()?;
        Ok(Self {
            profile: file.profile,
            denied,
        })
    }

    #[cfg(test)]
    pub fn for_tests(profile: Profile) -> Self {
        Self {
            profile,
            denied: vec![Cidr::parse("10.42.0.0/16").expect("valid range")],
        }
    }

    pub fn profile(&self) -> Profile {
        self.profile
    }

    /// Whether this port may be connected to. v1 is the default web ports; a
    /// test policy also allows the ephemeral port a fixture server binds.
    pub fn permits_port(&self, port: u16) -> bool {
        matches!(port, 80 | 443) || self.profile == Profile::Test
    }

    /// Whether this address may be connected to.
    pub fn permits(&self, address: IpAddr) -> bool {
        let address = canonical(address);
        if self.denied.iter().any(|range| range.contains(address)) {
            return false;
        }
        if is_public(address) {
            return true;
        }
        // The only relaxation, and only for a policy an operator explicitly
        // marked as a test policy.
        self.profile == Profile::Test && address.is_loopback()
    }
}

/// Reduce the IPv6 spellings of an IPv4 address to that address, so one
/// classification covers `127.0.0.1`, `::ffff:127.0.0.1`, `::127.0.0.1`,
/// `2002:7f00:1::`, and `64:ff9b::7f00:1` alike.
pub fn canonical(address: IpAddr) -> IpAddr {
    let IpAddr::V6(v6) = address else {
        return address;
    };
    let segments = v6.segments();
    if let Some(v4) = v6.to_ipv4_mapped() {
        return IpAddr::V4(v4);
    }
    // IPv4-compatible (deprecated) addresses, excluding :: and ::1.
    if segments[..6] == [0, 0, 0, 0, 0, 0] && !v6.is_unspecified() && !v6.is_loopback() {
        return IpAddr::V4(Ipv4Addr::new(
            (segments[6] >> 8) as u8,
            segments[6] as u8,
            (segments[7] >> 8) as u8,
            segments[7] as u8,
        ));
    }
    // 6to4 (2002::/16) carries the v4 address in the next 32 bits.
    if segments[0] == 0x2002 {
        return IpAddr::V4(Ipv4Addr::new(
            (segments[1] >> 8) as u8,
            segments[1] as u8,
            (segments[2] >> 8) as u8,
            segments[2] as u8,
        ));
    }
    // NAT64 well-known prefix 64:ff9b::/96.
    if segments[..6] == [0x0064, 0xff9b, 0, 0, 0, 0] {
        return IpAddr::V4(Ipv4Addr::new(
            (segments[6] >> 8) as u8,
            segments[6] as u8,
            (segments[7] >> 8) as u8,
            segments[7] as u8,
        ));
    }
    address
}

/// Whether an address is ordinary public Internet space.
///
/// Written as an allowlist of "not special" rather than a blocklist of the
/// ranges someone remembered.
pub fn is_public(address: IpAddr) -> bool {
    match canonical(address) {
        IpAddr::V4(v4) => is_public_v4(v4),
        IpAddr::V6(v6) => is_public_v6(v6),
    }
}

fn is_public_v4(address: Ipv4Addr) -> bool {
    let [a, b, c, _] = address.octets();
    let special = address.is_unspecified()
        || address.is_loopback()
        || address.is_private()
        || address.is_link_local()          // 169.254/16, cloud metadata included
        || address.is_multicast()
        || address.is_broadcast()
        || address.is_documentation()
        || a == 0                           // "this network"
        || (a == 100 && (64..128).contains(&b)) // CGNAT 100.64/10
        || (a == 192 && b == 0 && c == 0)   // IETF protocol assignments
        || (a == 192 && b == 88 && c == 99) // 6to4 relay anycast
        || (a == 198 && (b == 18 || b == 19)) // benchmarking
        || a >= 240; // reserved and broadcast
    !special
}

fn is_public_v6(address: Ipv6Addr) -> bool {
    let segments = address.segments();
    let special = address.is_unspecified()
        || address.is_loopback()
        || address.is_multicast()
        || (segments[0] & 0xfe00) == 0xfc00 // unique local fc00::/7
        || (segments[0] & 0xffc0) == 0xfe80 // link local fe80::/10
        || (segments[0] & 0xffc0) == 0xfec0 // deprecated site local
        || segments[0] == 0x2001 && segments[1] == 0x0db8 // documentation
        || segments[0] == 0x2001 && segments[1] == 0x0000 // Teredo
        || segments[..4] == [0x0100, 0, 0, 0]; // discard-only 100::/64
    !special
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> DestinationPolicy {
        DestinationPolicy::for_tests(Profile::Production)
    }

    fn address(value: &str) -> IpAddr {
        value.parse().expect("test address")
    }

    #[test]
    fn public_addresses_are_permitted() {
        assert!(policy().permits(address("93.184.216.34")));
        assert!(policy().permits(address("2606:2800:220:1:248:1893:25c8:1946")));
    }

    #[test]
    fn non_public_v4_space_is_refused() {
        for value in [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.4.4",
            "192.168.1.1",
            "169.254.169.254", // cloud metadata
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "255.255.255.255",
            "198.18.0.1",
            "192.0.2.5",
        ] {
            assert!(
                !policy().permits(address(value)),
                "{value} should be refused"
            );
        }
    }

    #[test]
    fn ipv6_spellings_of_refused_v4_space_are_refused_too() {
        for value in [
            "::1",
            "::ffff:169.254.169.254", // v4-mapped metadata
            "::ffff:10.0.0.1",
            "::169.254.169.254", // v4-compatible
            "2002:a9fe:a9fe::",  // 6to4 wrapping 169.254.169.254
            "64:ff9b::a00:1",    // NAT64 wrapping 10.0.0.1
            "fd00::1",           // unique local
            "fe80::1",           // link local
            "2001:db8::1",       // documentation
        ] {
            assert!(
                !policy().permits(address(value)),
                "{value} should be refused"
            );
        }
    }

    #[test]
    fn configured_cluster_ranges_are_refused_even_though_private_space_already_is() {
        // The range is inside RFC 1918 space, so this proves the operator list is
        // consulted rather than only the built-ins.
        assert!(!policy().permits(address("10.42.0.7")));
        assert!(!policy().permits(address("::ffff:10.42.0.7")));
    }

    #[test]
    fn only_the_default_web_ports_are_fetchable() {
        assert!(policy().permits_port(80));
        assert!(policy().permits_port(443));
        assert!(!policy().permits_port(8443));
        assert!(!policy().permits_port(22));
    }

    #[test]
    fn a_test_profile_permits_loopback_and_nothing_else_extra() {
        let policy = DestinationPolicy::for_tests(Profile::Test);
        assert!(policy.permits(address("127.0.0.1")));
        assert!(policy.permits(address("::1")));
        assert!(!policy.permits(address("169.254.169.254")));
        assert!(!policy.permits(address("10.42.0.7")));
    }

    #[test]
    fn a_policy_without_cluster_ranges_does_not_load() {
        let directory =
            std::env::temp_dir().join(format!("webfetch-policy-{}", std::process::id()));
        std::fs::create_dir_all(&directory).expect("temp dir");
        let path = directory.join("empty.json");
        std::fs::write(
            &path,
            br#"{"policy_version":1,"profile":"production","denied_cidrs":[]}"#,
        )
        .expect("write");
        assert!(DestinationPolicy::load(&path).is_err());

        std::fs::write(
            &path,
            br#"{"policy_version":1,"profile":"production","denied_cidrs":["10.42.0.0"]}"#,
        )
        .expect("write");
        assert!(DestinationPolicy::load(&path).is_err());

        assert!(DestinationPolicy::load(&directory.join("absent.json")).is_err());
        std::fs::remove_dir_all(&directory).ok();
    }
}

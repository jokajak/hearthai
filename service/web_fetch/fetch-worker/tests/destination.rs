//! Destination policy: what the fetcher will and will not connect to.

use std::net::IpAddr;

use hearthai_web_fetch_worker::destination::{DestinationPolicy, PolicyError};

fn policy() -> DestinationPolicy {
    DestinationPolicy::from_json(Some(r#"{"cluster_denied_cidrs":["10.42.0.0/16","fd12:3456::/32"]}"#))
        .unwrap()
}

fn address(value: &str) -> IpAddr {
    value.parse().unwrap()
}

#[test]
fn an_absent_empty_or_malformed_policy_leaves_the_capability_unavailable() {
    assert_eq!(DestinationPolicy::from_json(None).unwrap_err(), PolicyError::Missing);
    assert_eq!(
        DestinationPolicy::from_json(Some(r#"{"cluster_denied_cidrs":[]}"#)).unwrap_err(),
        PolicyError::Empty
    );
    for malformed in [
        "{",
        "[]",
        r#"{"cluster_denied_cidrs":"10.0.0.0/8"}"#,
        r#"{"cluster_denied_cidrs":["10.0.0.0/33"]}"#,
        r#"{"cluster_denied_cidrs":["not-a-cidr"]}"#,
        r#"{"cluster_denied_cidrs":["10.0.0.0/8"],"allow_anything":true}"#,
    ] {
        assert_eq!(
            DestinationPolicy::from_json(Some(malformed)).unwrap_err(),
            PolicyError::Malformed,
            "accepted {malformed}"
        );
    }
}

#[test]
fn a_failed_update_leaves_the_previous_policy_enforcing() {
    let mut active = policy();
    assert!(active.replace(Some(r#"{"cluster_denied_cidrs":["10.0.0.0/8","bad"]}"#)).is_err());
    // The half-valid update named a wider range; if it had been applied
    // piecewise, 10.0.0.1 would now be denied by the new range and 10.42.0.1
    // permitted by the absence of the old one.
    assert!(!active.permits(address("10.42.0.1")), "the previous cluster range stopped being enforced");

    assert!(active.replace(Some(r#"{"cluster_denied_cidrs":["172.31.0.0/16"]}"#)).is_ok());
    assert!(!active.permits(address("172.31.9.9")));
}

#[test]
fn non_public_addresses_are_denied_in_every_spelling() {
    let policy = policy();
    for denied in [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254", // the cloud metadata endpoint
        "0.0.0.0",
        "100.64.1.1", // carrier-grade NAT
        "192.0.2.1",  // documentation
        "255.255.255.255",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00::abcd",
        "ff02::1",
        "2001:db8::1",
        "::ffff:169.254.169.254", // v4-mapped metadata endpoint
        "::ffff:10.0.0.1",
        "::10.0.0.1",        // v4-compatible
        "2002:a9fe:a9fe::1", // 6to4 wrapping 169.254.169.254
        "2001::1",           // Teredo
    ] {
        assert!(!policy.permits(address(denied)), "permitted {denied}");
    }
}

#[test]
fn ordinary_public_addresses_are_permitted() {
    let policy = policy();
    for permitted in ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "2001:4860:4860::8888"] {
        assert!(policy.permits(address(permitted)), "denied {permitted}");
    }
}

#[test]
fn configured_cluster_ranges_are_denied_even_though_nothing_else_would_catch_them() {
    let policy = DestinationPolicy::from_json(Some(
        r#"{"cluster_denied_cidrs":["198.51.100.0/24","2600:1f18::/32"]}"#,
    ))
    .unwrap();
    // Public by classification, private to this deployment.
    assert!(!policy.permits(address("2600:1f18::5")));
    assert!(policy.permits(address("2600:1f19::5")));
}

#[test]
fn one_private_answer_poisons_the_whole_name() {
    let policy = policy();
    let mixed = [address("93.184.216.34"), address("10.42.0.7")];
    assert!(!policy.permits_all(&mixed), "a mixed DNS answer was accepted");
    assert!(!policy.permits_all(&[]), "a name with no answers was accepted");
    assert!(policy.permits_all(&[address("93.184.216.34"), address("1.1.1.1")]));
}

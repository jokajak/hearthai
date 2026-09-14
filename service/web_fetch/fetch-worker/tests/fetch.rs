//! The fetch loop: redirects, rebinding, limits, media types, deadlines.

mod support;

use std::time::Duration;

use hearthai_web_fetch_contracts::WebFetchRequest;
use hearthai_web_fetch_worker::clock::SystemClock;
use hearthai_web_fetch_worker::destination::DestinationPolicy;
use hearthai_web_fetch_worker::{FetchError, FetchLimits, Fetcher};
use support::{Reply, ScriptedConnector, ScriptedResolver, StepClock};

use hearthai_web_fetch_contracts::ErrorCode;

fn policy() -> DestinationPolicy {
    DestinationPolicy::from_json(Some(r#"{"cluster_denied_cidrs":["10.42.0.0/16"]}"#)).unwrap()
}

fn request(url: &str) -> WebFetchRequest {
    WebFetchRequest::parse(&format!(r#"{{"url":"{url}"}}"#)).unwrap()
}

#[test]
fn a_permitted_page_is_downloaded_once() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![("https://example.com/page", Reply::html("<h1>Hi</h1>"))]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    let outcome = fetcher.fetch(&request("https://example.com/page")).unwrap();
    assert_eq!(outcome.http_status, 200);
    assert_eq!(outcome.media_type, "text/html");
    assert_eq!(outcome.charset.as_deref(), Some("utf-8"));
    assert_eq!(outcome.body, b"<h1>Hi</h1>");
    assert_eq!(outcome.redirects, 0);
    assert_eq!(connector.contacted.lock().unwrap().len(), 1);
}

#[test]
fn the_connection_goes_to_the_address_that_was_validated() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![("https://example.com/page", Reply::html("<p>x</p>"))]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    fetcher.fetch(&request("https://example.com/page")).unwrap();
    assert_eq!(connector.addresses_contacted(), vec!["93.184.216.34".parse::<std::net::IpAddr>().unwrap()]);
}

#[test]
fn a_redirect_is_re_resolved_and_re_validated_before_it_is_followed() {
    let mut resolver = ScriptedResolver::default();
    resolver.add("example.com", &["93.184.216.34"]).add("cdn.example.net", &["1.1.1.1"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/page", Reply::redirect(302, "https://cdn.example.net/real")),
        ("https://cdn.example.net/real", Reply::html("<p>moved</p>")),
    ]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    let outcome = fetcher.fetch(&request("https://example.com/page")).unwrap();
    assert_eq!(outcome.final_url.as_str(), "https://cdn.example.net/real");
    assert_eq!(outcome.redirects, 1);
    assert_eq!(resolver.looked_up.lock().unwrap().clone(), vec!["example.com", "cdn.example.net"]);
}

#[test]
fn a_relative_redirect_stays_on_the_hop_that_issued_it() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/a/b", Reply::redirect(301, "../c")),
        ("https://example.com/c", Reply::html("<p>c</p>")),
    ]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    let outcome = fetcher.fetch(&request("https://example.com/a/b")).unwrap();
    assert_eq!(outcome.final_url.as_str(), "https://example.com/c");
}

#[test]
fn a_redirect_into_private_space_is_refused_without_being_contacted() {
    let mut resolver = ScriptedResolver::default();
    resolver.add("example.com", &["93.184.216.34"]).add("internal.example.com", &["10.42.0.7"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/page", Reply::redirect(302, "https://internal.example.com/secret")),
        ("https://internal.example.com/secret", Reply::html("<p>internal</p>")),
    ]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/page")).unwrap_err(), FetchError::UnsafeSource);
    let contacted = connector.contacted.lock().unwrap();
    assert_eq!(contacted.len(), 1, "the forbidden destination was contacted");
    assert_eq!(contacted[0].0, "https://example.com/page");
}

#[test]
fn a_redirect_to_the_metadata_endpoint_is_refused() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/page", Reply::redirect(307, "http://169.254.169.254/latest/meta-data/")),
        ("http://169.254.169.254/latest/meta-data/", Reply::html("<p>creds</p>")),
    ]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/page")).unwrap_err(), FetchError::UnsafeSource);
    assert_eq!(connector.contacted.lock().unwrap().len(), 1);
}

#[test]
fn a_name_that_answers_publicly_then_privately_never_reaches_the_private_answer() {
    // The rebinding shape: the first lookup is clean, the second is not. The
    // second lookup happens because the redirect forces one, and the address
    // handed to the connector is always from the answer that was just checked.
    let mut resolver = ScriptedResolver::default();
    resolver
        .add("example.com", &["93.184.216.34"])
        .add("rebind.example.com", &["93.184.216.34", "10.42.0.9"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/page", Reply::redirect(302, "https://rebind.example.com/next")),
        ("https://rebind.example.com/next", Reply::html("<p>x</p>")),
    ]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/page")).unwrap_err(), FetchError::UnsafeSource);
    assert!(!connector.addresses_contacted().contains(&"10.42.0.9".parse().unwrap()));
}

#[test]
fn a_url_naming_a_private_address_directly_is_refused_without_a_lookup() {
    let resolver = ScriptedResolver::default();
    let connector = ScriptedConnector::new(vec![]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    for url in ["http://169.254.169.254/latest/", "http://[::ffff:10.42.0.1]/", "http://[fd00::1]/"] {
        assert_eq!(fetcher.fetch(&request(url)).unwrap_err(), FetchError::UnsafeSource, "{url}");
    }
    assert!(connector.contacted.lock().unwrap().is_empty());
    assert!(resolver.looked_up.lock().unwrap().is_empty());
}

#[test]
fn the_redirect_chain_is_bounded() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![(
        "https://example.com/loop",
        Reply::redirect(302, "https://example.com/loop"),
    )]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/loop")).unwrap_err(), FetchError::ResponseLimit);
    // Three redirects means four requests, and then it stops.
    assert_eq!(connector.contacted.lock().unwrap().len(), 4);
}

#[test]
fn an_oversized_body_is_refused_rather_than_truncated() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let limits = FetchLimits { max_wire_body_bytes: 64, ..FetchLimits::default() };
    let connector =
        ScriptedConnector::new(vec![("https://example.com/big", Reply::typed("text/plain", &[b'a'; 65]))]);
    let clock = SystemClock;
    let fetcher =
        Fetcher { policy: &policy(), resolver: &resolver, connector: &connector, clock: &clock, limits };

    assert_eq!(fetcher.fetch(&request("https://example.com/big")).unwrap_err(), FetchError::ResponseLimit);
}

#[test]
fn a_body_exactly_at_the_ceiling_is_still_accepted() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let limits = FetchLimits { max_wire_body_bytes: 64, ..FetchLimits::default() };
    let connector =
        ScriptedConnector::new(vec![("https://example.com/exact", Reply::typed("text/plain", &[b'a'; 64]))]);
    let clock = SystemClock;
    let fetcher =
        Fetcher { policy: &policy(), resolver: &resolver, connector: &connector, clock: &clock, limits };

    assert_eq!(fetcher.fetch(&request("https://example.com/exact")).unwrap().body.len(), 64);
}

#[test]
fn oversized_headers_are_refused() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let connector = ScriptedConnector::new(vec![(
        "https://example.com/page",
        Reply::html("<p>x</p>").with_header_bytes(32 * 1024 + 1),
    )]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/page")).unwrap_err(), FetchError::ResponseLimit);
}

#[test]
fn only_the_supported_text_media_types_are_downloaded() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let clock = SystemClock;
    let policy = policy();

    for (content_type, supported) in [
        ("text/html; charset=utf-8", true),
        ("text/plain", true),
        ("application/json", true),
        ("application/xhtml+xml", true),
        ("text/markdown; charset=UTF-8", true),
        ("application/pdf", false),
        ("image/png", false),
        ("application/zip", false),
        ("application/octet-stream", false),
        ("not a media type", false),
        ("text/html; charset=", false),
    ] {
        let connector =
            ScriptedConnector::new(vec![("https://example.com/doc", Reply::typed(content_type, b"body"))]);
        let fetcher = Fetcher {
            policy: &policy,
            resolver: &resolver,
            connector: &connector,
            clock: &clock,
            limits: FetchLimits::default(),
        };
        let outcome = fetcher.fetch(&request("https://example.com/doc"));
        assert_eq!(outcome.is_ok(), supported, "{content_type}");
        if !supported {
            assert_eq!(outcome.unwrap_err(), FetchError::UnsupportedContent);
        }
    }
}

#[test]
fn a_response_with_no_content_type_is_not_guessed_at() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let mut reply = Reply::html("<p>x</p>");
    reply.content_type = None;
    let connector = ScriptedConnector::new(vec![("https://example.com/page", reply)]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(
        fetcher.fetch(&request("https://example.com/page")).unwrap_err(),
        FetchError::UnsupportedContent
    );
}

#[test]
fn a_slow_chain_runs_out_of_network_time() {
    let mut resolver = ScriptedResolver::default();
    resolver
        .add("example.com", &["93.184.216.34"])
        .add("second.example.com", &["1.1.1.1"])
        .add("third.example.com", &["1.0.0.1"]);
    let connector = ScriptedConnector::new(vec![
        ("https://example.com/a", Reply::redirect(302, "https://second.example.com/b")),
        ("https://second.example.com/b", Reply::redirect(302, "https://third.example.com/c")),
        ("https://third.example.com/c", Reply::html("<p>too late</p>")),
    ]);
    // Every clock reading advances six seconds, so the budget is gone partway
    // through the chain rather than at a convenient boundary.
    let clock = StepClock::new(Duration::from_secs(6));
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    assert_eq!(fetcher.fetch(&request("https://example.com/a")).unwrap_err(), FetchError::Deadline);
}

#[test]
fn a_4xx_body_is_still_a_response() {
    let resolver = ScriptedResolver::with("example.com", &["93.184.216.34"]);
    let mut reply = Reply::typed("text/plain", b"Not Found");
    reply.status = 404;
    let connector = ScriptedConnector::new(vec![("https://example.com/missing", reply)]);
    let clock = SystemClock;
    let fetcher = Fetcher {
        policy: &policy(),
        resolver: &resolver,
        connector: &connector,
        clock: &clock,
        limits: FetchLimits::default(),
    };

    let outcome = fetcher.fetch(&request("https://example.com/missing")).unwrap();
    assert_eq!(outcome.http_status, 404);
    assert_eq!(outcome.body, b"Not Found");
}

#[test]
fn every_stage_failure_maps_onto_one_public_code() {
    assert_eq!(FetchError::UnsafeSource.code(), ErrorCode::UnsafeSource);
    assert_eq!(FetchError::PolicyUnavailable.code(), ErrorCode::UnsafeSource);
    assert_eq!(FetchError::Network.code(), ErrorCode::FetchFailed);
    assert_eq!(FetchError::Storage.code(), ErrorCode::FetchFailed);
    assert_eq!(FetchError::ResponseLimit.code(), ErrorCode::ResponseLimitExceeded);
    assert_eq!(FetchError::UnsupportedContent.code(), ErrorCode::UnsupportedContent);
    assert_eq!(FetchError::Deadline.code(), ErrorCode::DeadlineExceeded);
}

//! `webfetch-fetch`: the network stage, as a one-shot process.
//!
//! Reads a validated request, seals what it produced into the run's handoff,
//! exits. It prints no response content, on any path, ever.

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use clap::Parser;
use hearthai_webfetch_contracts::envelope::ErrorCode;
use hearthai_webfetch_contracts::handoff::{Handoff, HandoffKey};
use hearthai_webfetch_contracts::web_fetch::WebFetchRequest;
use hearthai_webfetch_fetch_worker::fetch::Fetcher;
use hearthai_webfetch_fetch_worker::policy::{DestinationPolicy, Profile};
use hearthai_webfetch_fetch_worker::resolve::SystemResolver;

/// Exit code for "this stage could not run at all", as opposed to "this fetch
/// did not succeed". The controller treats them differently: the first is a
/// misconfigured capability, the second is a result for the next stage.
const UNAVAILABLE: u8 = 2;

#[derive(Debug, Parser)]
#[command(
    name = "webfetch-fetch",
    about = "Fetch one URL into a sealed run handoff for the inspection stage.",
    long_about = None
)]
struct Arguments {
    /// Run this fetch belongs to.
    #[arg(long, value_parser = run_id)]
    run_id: String,
    /// JSON file holding one web_fetch:v1 request.
    #[arg(long)]
    request: PathBuf,
    /// JSON file holding this deployment's destination policy.
    #[arg(long)]
    policy: PathBuf,
    /// Directory to write the sealed handoff into.
    #[arg(long)]
    artifact_dir: PathBuf,
    /// File holding the run-scoped key shared with the inspection stage.
    #[arg(long)]
    handoff_key: PathBuf,
}

fn run_id(value: &str) -> Result<String, String> {
    let shaped = !value.is_empty()
        && value.len() <= 64
        && value
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if shaped {
        Ok(value.to_string())
    } else {
        Err("must be 1-64 characters of [a-z0-9-]".to_string())
    }
}

fn main() -> ExitCode {
    let arguments = Arguments::parse();

    // Configuration problems are not fetch outcomes: they leave the capability
    // unavailable, and nothing is written into the run's handoff.
    let policy = match DestinationPolicy::load(&arguments.policy) {
        Ok(policy) => policy,
        Err(error) => return unavailable("destination policy unusable", &error.to_string()),
    };
    if policy.profile() == Profile::Test {
        eprintln!(
            "warning: running under a test destination policy; non-public destinations are reachable"
        );
    }
    let key = match HandoffKey::load(&arguments.handoff_key) {
        Ok(key) => key,
        Err(error) => return unavailable("handoff key unusable", &error.to_string()),
    };
    let handoff = Handoff::new(&arguments.artifact_dir, &arguments.run_id, key);

    let raw = match std::fs::read_to_string(&arguments.request) {
        Ok(raw) => raw,
        Err(error) => return unavailable("request unreadable", &error.to_string()),
    };
    // Unknown fields are refused here, so a caller cannot smuggle headers,
    // credentials, rules, or a skip-scan flag past this point.
    let request: WebFetchRequest = match serde_json::from_str(&raw) {
        Ok(request) => request,
        Err(error) => {
            return unavailable(
                "request is not a valid web_fetch:v1 request",
                &error.to_string(),
            );
        }
    };

    let fetcher = Fetcher::new(Arc::new(policy), Arc::new(SystemResolver));
    let outcome = match fetcher.fetch(&arguments.run_id, &request) {
        Ok(fetched) => handoff
            .write_fetched(&fetched.artifact, &fetched.body)
            .map_err(|error| {
                eprintln!("handoff could not be written: {error}");
                ErrorCode::InternalError
            })
            .map(|()| ExitCode::SUCCESS),
        Err(code) => Err(code),
    };

    match outcome {
        Ok(success) => success,
        Err(code) => match handoff.write_failure(code) {
            Ok(()) => ExitCode::from(1),
            Err(error) => unavailable("handoff could not be written", &error.to_string()),
        },
    }
}

fn unavailable(what: &str, detail: &str) -> ExitCode {
    eprintln!("{what}: {detail}");
    ExitCode::from(UNAVAILABLE)
}

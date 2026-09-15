//! `webfetch-fetch`: the network stage, as a one-shot process.
//!
//! Reads a validated request, writes an artifact and a stage outcome, exits.
//! It prints no response content, on any path, ever.

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use hearthai_webfetch_contracts::envelope::ErrorCode;
use hearthai_webfetch_contracts::stage::{Stage, StageOutcome};
use hearthai_webfetch_contracts::web_fetch::WebFetchRequest;
use hearthai_webfetch_fetch_worker::fetch::Fetcher;
use hearthai_webfetch_fetch_worker::policy::{DestinationPolicy, Profile};
use hearthai_webfetch_fetch_worker::resolve::SystemResolver;

const USAGE: &str =
    "usage: webfetch-fetch --run-id ID --request FILE --policy FILE --artifact-dir DIR";

struct Arguments {
    run_id: String,
    request: PathBuf,
    policy: PathBuf,
    artifact_dir: PathBuf,
}

fn main() -> ExitCode {
    let arguments = match parse_arguments() {
        Ok(arguments) => arguments,
        Err(message) => {
            eprintln!("{message}\n{USAGE}");
            return ExitCode::from(2);
        }
    };

    // A configuration problem is not a fetch outcome: it leaves the capability
    // unavailable, and nothing is written into the run's handoff directory.
    let policy = match DestinationPolicy::load(&arguments.policy) {
        Ok(policy) => policy,
        Err(error) => {
            eprintln!("destination policy unusable: {error}");
            return ExitCode::from(2);
        }
    };
    if policy.profile() == Profile::Test {
        eprintln!(
            "warning: running under a test destination policy; non-public destinations are reachable"
        );
    }

    let raw = match std::fs::read_to_string(&arguments.request) {
        Ok(raw) => raw,
        Err(error) => {
            eprintln!("request unreadable: {error}");
            return ExitCode::from(2);
        }
    };
    // Unknown fields are refused here, so a caller cannot smuggle headers,
    // credentials, rules, or a skip-scan flag past this point.
    let request: WebFetchRequest = match serde_json::from_str(&raw) {
        Ok(request) => request,
        Err(error) => {
            eprintln!("request is not a valid web_fetch:v1 request: {error}");
            return ExitCode::from(2);
        }
    };

    let fetcher = Fetcher::new(Arc::new(policy), Arc::new(SystemResolver));
    match fetcher.fetch(&arguments.run_id, &request) {
        Ok(fetched) => {
            if let Err(error) = fetched
                .artifact
                .write(&arguments.artifact_dir, &fetched.body)
            {
                eprintln!("artifact could not be written: {error}");
                return report(&arguments, ErrorCode::InternalError);
            }
            let outcome = StageOutcome::ok(Stage::Fetch, &arguments.run_id);
            if let Err(error) = outcome.write(&arguments.artifact_dir) {
                eprintln!("stage outcome could not be written: {error}");
                return ExitCode::from(2);
            }
            ExitCode::SUCCESS
        }
        Err(code) => report(&arguments, code),
    }
}

/// Record a public failure code for the inspection stage to turn into an envelope.
fn report(arguments: &Arguments, code: ErrorCode) -> ExitCode {
    let outcome = StageOutcome::failed(Stage::Fetch, &arguments.run_id, code);
    if let Err(error) = outcome.write(&arguments.artifact_dir) {
        eprintln!("stage outcome could not be written: {error}");
        return ExitCode::from(2);
    }
    ExitCode::from(1)
}

fn parse_arguments() -> Result<Arguments, String> {
    let mut run_id = None;
    let mut request = None;
    let mut policy = None;
    let mut artifact_dir = None;
    let mut arguments = std::env::args().skip(1);
    while let Some(flag) = arguments.next() {
        let mut value = || {
            arguments
                .next()
                .ok_or_else(|| format!("{flag} needs a value"))
        };
        match flag.as_str() {
            "--run-id" => run_id = Some(value()?),
            "--request" => request = Some(PathBuf::from(value()?)),
            "--policy" => policy = Some(PathBuf::from(value()?)),
            "--artifact-dir" => artifact_dir = Some(PathBuf::from(value()?)),
            "--help" | "-h" => return Err(String::new()),
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    let run_id = run_id.ok_or("--run-id is required")?;
    if !is_run_id(&run_id) {
        return Err("--run-id must be 1-64 characters of [a-z0-9-]".to_string());
    }
    Ok(Arguments {
        run_id,
        request: request.ok_or("--request is required")?,
        policy: policy.ok_or("--policy is required")?,
        artifact_dir: artifact_dir.ok_or("--artifact-dir is required")?,
    })
}

fn is_run_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

//! `webfetch-inspect`: the offline stage, as a one-shot process.
//!
//! It is the only component that emits an envelope. It needs no network, no
//! credentials, and no tools; give it a run's artifact directory and a rule
//! bundle, and it returns exactly one validated result on stdout.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use hearthai_webfetch_contracts::artifact::BODY_FILE;
use hearthai_webfetch_contracts::envelope::{ErrorCode, Inspection, Status};
use hearthai_webfetch_contracts::web_fetch::WebFetchEnvelope;
use hearthai_webfetch_inspector::detect::RuleBundle;
use hearthai_webfetch_inspector::pipeline::Inspector;

const USAGE: &str = "usage: webfetch-inspect --run-id ID --artifact-dir DIR --rules DIR [--out FILE] [--keep-artifact]";

struct Arguments {
    run_id: String,
    artifact_dir: PathBuf,
    rules: PathBuf,
    out: Option<PathBuf>,
    keep_artifact: bool,
}

fn main() -> ExitCode {
    let arguments = match parse_arguments() {
        Ok(arguments) => arguments,
        Err(message) => {
            eprintln!("{message}\n{USAGE}");
            return ExitCode::from(2);
        }
    };

    // No valid bundle means the capability is unavailable. The run in flight
    // still gets a well-formed answer, and that answer contains no content.
    let bundle = match RuleBundle::load(&arguments.rules) {
        Ok(bundle) => bundle,
        Err(error) => {
            eprintln!("rule bundle unusable: {error}");
            let envelope =
                WebFetchEnvelope::failure(ErrorCode::InspectionFailed, Inspection::not_run())
                    .expect("inspection_failed with not_run is a valid envelope");
            let _ = emit(&arguments, &envelope);
            return ExitCode::from(2);
        }
    };

    let outcome = Inspector::new(&bundle).inspect(&arguments.run_id, &arguments.artifact_dir);

    // The audit line is for the operator: outcome, rule identifiers, counts and
    // timings, and nothing from the response itself.
    match serde_json::to_string(&outcome.audit) {
        Ok(audit) => eprintln!("{audit}"),
        Err(error) => eprintln!("audit record could not be serialized: {error}"),
    }

    if !arguments.keep_artifact {
        // Temporary response data does not outlive the stage that needed it.
        fs::remove_file(arguments.artifact_dir.join(BODY_FILE)).ok();
    }

    if let Err(error) = emit(&arguments, &outcome.envelope) {
        eprintln!("result could not be written: {error}");
        return ExitCode::from(2);
    }
    match outcome.envelope.status {
        Status::Ok => ExitCode::SUCCESS,
        _ => ExitCode::from(1),
    }
}

fn emit(arguments: &Arguments, envelope: &WebFetchEnvelope) -> Result<(), String> {
    let encoded = envelope.to_json().map_err(|error| error.to_string())?;
    match &arguments.out {
        Some(path) => write_result(path, &encoded),
        None => {
            println!("{encoded}");
            Ok(())
        }
    }
}

fn write_result(path: &Path, encoded: &str) -> Result<(), String> {
    fs::write(path, encoded).map_err(|error| error.to_string())
}

fn parse_arguments() -> Result<Arguments, String> {
    let mut run_id = None;
    let mut artifact_dir = None;
    let mut rules = None;
    let mut out = None;
    let mut keep_artifact = false;
    let mut arguments = std::env::args().skip(1);
    while let Some(flag) = arguments.next() {
        let mut value = || {
            arguments
                .next()
                .ok_or_else(|| format!("{flag} needs a value"))
        };
        match flag.as_str() {
            "--run-id" => run_id = Some(value()?),
            "--artifact-dir" => artifact_dir = Some(PathBuf::from(value()?)),
            "--rules" => rules = Some(PathBuf::from(value()?)),
            "--out" => out = Some(PathBuf::from(value()?)),
            "--keep-artifact" => keep_artifact = true,
            "--help" | "-h" => return Err(String::new()),
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok(Arguments {
        run_id: run_id.ok_or("--run-id is required")?,
        artifact_dir: artifact_dir.ok_or("--artifact-dir is required")?,
        rules: rules.ok_or("--rules is required")?,
        out,
        keep_artifact,
    })
}

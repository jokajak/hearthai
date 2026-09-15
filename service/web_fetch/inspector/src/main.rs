//! `webfetch-inspect`: the offline stage, as a one-shot process.
//!
//! It is the only component that emits an envelope. It needs no network, no
//! credentials, and no tools; give it a run's sealed handoff and a rule bundle,
//! and it returns exactly one validated result.

use std::fs;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::Parser;
use hearthai_webfetch_contracts::envelope::{ErrorCode, Inspection, Status};
use hearthai_webfetch_contracts::handoff::{Handoff, HandoffKey};
use hearthai_webfetch_contracts::web_fetch::WebFetchEnvelope;
use hearthai_webfetch_inspector::detect::RuleBundle;
use hearthai_webfetch_inspector::pipeline::Inspector;

/// Exit code for "this stage could not produce a result", as opposed to "the
/// result withholds content".
const UNAVAILABLE: u8 = 2;

#[derive(Debug, Parser)]
#[command(
    name = "webfetch-inspect",
    about = "Inspect one sealed run handoff and emit exactly one web_fetch:v1 envelope.",
    long_about = None
)]
struct Arguments {
    /// Run this handoff belongs to.
    #[arg(long)]
    run_id: String,
    /// Directory the fetch stage sealed.
    #[arg(long)]
    artifact_dir: PathBuf,
    /// Directory holding the compiled rule bundle.
    #[arg(long)]
    rules: PathBuf,
    /// File holding the run-scoped key the fetch stage sealed with.
    #[arg(long)]
    handoff_key: PathBuf,
    /// Where to write the envelope. Defaults to stdout.
    #[arg(long)]
    out: Option<PathBuf>,
    /// Keep the stored response after inspection, for debugging.
    #[arg(long)]
    keep_artifact: bool,
}

fn main() -> ExitCode {
    let arguments = Arguments::parse();

    let key = match HandoffKey::load(&arguments.handoff_key) {
        Ok(key) => key,
        Err(error) => {
            eprintln!("handoff key unusable: {error}");
            // The run still gets a well-formed answer, and it contains no content.
            return emit_and_exit(&arguments, ErrorCode::InternalError, Inspection::not_run());
        }
    };

    // No valid bundle means the capability is unavailable. The inspection began
    // and could not be completed, which is a different claim from never having
    // run, and only one of them is true here.
    let bundle = match RuleBundle::load(&arguments.rules) {
        Ok(bundle) => bundle,
        Err(error) => {
            eprintln!("rule bundle unusable: {error}");
            return emit_and_exit(
                &arguments,
                ErrorCode::InspectionFailed,
                Inspection::failed(None),
            );
        }
    };

    let handoff = Handoff::new(&arguments.artifact_dir, &arguments.run_id, key);
    let outcome = Inspector::new(&bundle).inspect(&handoff);

    // The audit line is for the operator: outcome, rule identifiers, counts and
    // timings, and nothing from the response itself.
    match serde_json::to_string(&outcome.audit) {
        Ok(audit) => eprintln!("{audit}"),
        Err(error) => eprintln!("audit record could not be serialized: {error}"),
    }

    if !arguments.keep_artifact {
        // Temporary response data does not outlive the stage that needed it.
        handoff.remove_body();
    }

    if let Err(error) = emit(&arguments, &outcome.envelope) {
        eprintln!("result could not be written: {error}");
        return ExitCode::from(UNAVAILABLE);
    }
    match outcome.envelope.status {
        Status::Ok => ExitCode::SUCCESS,
        _ => ExitCode::from(1),
    }
}

fn emit_and_exit(arguments: &Arguments, code: ErrorCode, inspection: Inspection) -> ExitCode {
    let envelope = WebFetchEnvelope::failure(code, inspection)
        .expect("a fixed code and status is a valid envelope");
    if let Err(error) = emit(arguments, &envelope) {
        eprintln!("result could not be written: {error}");
    }
    ExitCode::from(UNAVAILABLE)
}

fn emit(arguments: &Arguments, envelope: &WebFetchEnvelope) -> Result<(), String> {
    let encoded = envelope.to_json().map_err(|error| error.to_string())?;
    match &arguments.out {
        Some(path) => fs::write(path, &encoded).map_err(|error| error.to_string()),
        None => {
            println!("{encoded}");
            Ok(())
        }
    }
}

//! The detector: a pinned YARA-X engine behind three outcomes.
//!
//! No voting, no severity, no "warn and return". A match rejects the whole
//! response; an error withholds it. The only thing the rest of the pipeline may
//! ask this module is which of the three happened.

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use yara_x::{Compiler, Rules, Scanner};

use hearthai_webfetch_contracts::artifact::sha256_hex;
use hearthai_webfetch_contracts::limits::{MAX_SCAN_INPUT_BYTES, MAX_SCAN_MATCHES};
use hearthai_webfetch_contracts::{ContractError, Result};

/// Largest rule bundle that will be compiled.
const MAX_BUNDLE_BYTES: usize = 256 * 1024;
/// Largest number of rule files in one bundle.
const MAX_BUNDLE_FILES: usize = 64;
/// Wall clock allowed for compiling a bundle.
const COMPILE_BUDGET: Duration = Duration::from_secs(10);
/// Wall clock allowed for one scan.
const SCAN_TIMEOUT: Duration = Duration::from_secs(2);

/// What one scan concluded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    NoMatch,
    /// The identifiers that matched. They are recorded in bounded audit fields;
    /// they are never put in a caller-visible error.
    Match(Vec<String>),
}

/// The inspection could not be completed. Content is withheld either way, but
/// this is deliberately a different type from [`Verdict`] so that no code path
/// can treat "the scanner broke" as "nothing matched".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InspectionFailure(pub &'static str);

#[derive(Debug, Clone, PartialEq, Eq, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct BundleManifestFile {
    policy_version: u32,
    policy_id: String,
    description: String,
    maintenance: String,
}

/// A compiled, immutable rule bundle.
pub struct RuleBundle {
    policy_id: String,
    policy_digest: String,
    rule_count: usize,
    rules: Rules,
}

impl RuleBundle {
    /// Compile the bundle in `directory`.
    ///
    /// Everything that could make a bundle unusable - missing, empty, too large,
    /// not compiling - is an error here, and with no valid bundle the tool is
    /// unavailable rather than permissive.
    pub fn load(directory: &Path) -> Result<Self> {
        let policy_id = directory
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| ContractError::new("rule bundle directory has no name"))?
            .to_string();
        let manifest = fs::read_to_string(directory.join("bundle.json")).map_err(|error| {
            ContractError::new(format!("rule bundle manifest is unreadable: {error}"))
        })?;
        let manifest: BundleManifestFile = serde_json::from_str(&manifest).map_err(|error| {
            ContractError::new(format!("rule bundle manifest is not valid: {error}"))
        })?;
        if manifest.policy_version != 1 {
            return Err(ContractError::new("unsupported rule bundle version"));
        }
        // A bundle may not claim to be a policy it was not installed as, so the
        // manifest and the directory an operator mounted have to agree.
        if manifest.policy_id != policy_id {
            return Err(ContractError::new(
                "rule bundle manifest names a different policy",
            ));
        }

        let mut sources = Vec::new();
        let mut entries = fs::read_dir(directory)
            .map_err(|error| ContractError::new(format!("rule bundle is unreadable: {error}")))?
            .filter_map(std::result::Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|extension| extension == "yar"))
            .collect::<Vec<_>>();
        entries.sort();
        if entries.is_empty() {
            return Err(ContractError::new("rule bundle contains no rules"));
        }
        if entries.len() > MAX_BUNDLE_FILES {
            return Err(ContractError::new("rule bundle has too many files"));
        }
        let mut total = 0usize;
        for path in &entries {
            let source = fs::read_to_string(path)
                .map_err(|error| ContractError::new(format!("rule file is unreadable: {error}")))?;
            total += source.len();
            if total > MAX_BUNDLE_BYTES {
                return Err(ContractError::new("rule bundle is too large to compile"));
            }
            sources.push(source);
        }

        let started = Instant::now();
        let mut compiler = Compiler::new();
        // No includes and no modules: a bundle is exactly the files an operator
        // reviewed, and it cannot pull in anything else.
        compiler.enable_includes(false);
        let mut rule_count = 0usize;
        for source in &sources {
            compiler.add_source(source.as_str()).map_err(|error| {
                ContractError::new(format!("rule bundle does not compile: {error}"))
            })?;
            rule_count +=
                source.matches("\nrule ").count() + usize::from(source.starts_with("rule "));
        }
        if started.elapsed() > COMPILE_BUDGET {
            return Err(ContractError::new("rule bundle took too long to compile"));
        }
        let digest = sha256_hex(sources.concat().as_bytes());

        Ok(Self {
            policy_id,
            policy_digest: digest,
            rule_count,
            rules: compiler.build(),
        })
    }

    pub fn policy_id(&self) -> &str {
        &self.policy_id
    }

    /// Digest of the exact rule text in force, for the run's audit record.
    pub fn policy_digest(&self) -> &str {
        &self.policy_digest
    }

    pub fn rule_count(&self) -> usize {
        self.rule_count
    }

    pub fn detector(&self) -> Detector<'_> {
        let mut scanner = Scanner::new(&self.rules);
        scanner.set_timeout(SCAN_TIMEOUT);
        scanner.max_matches_per_pattern(MAX_SCAN_MATCHES);
        Detector {
            scanner,
            remaining_bytes: MAX_SCAN_INPUT_BYTES,
            scans: 0,
        }
    }
}

/// A scanner plus this response's total scan budget.
pub struct Detector<'bundle> {
    scanner: Scanner<'bundle>,
    remaining_bytes: usize,
    scans: usize,
}

impl Detector<'_> {
    /// Scan one input. Every required pass of the pipeline goes through here.
    pub fn scan(&mut self, data: &[u8]) -> std::result::Result<Verdict, InspectionFailure> {
        // Truncating to fit the budget would mean reporting a clean scan of part
        // of a response, so running out of budget is a failure instead.
        self.remaining_bytes = self
            .remaining_bytes
            .checked_sub(data.len())
            .ok_or(InspectionFailure("scan input budget exhausted"))?;
        self.scans += 1;
        let results = self
            .scanner
            .scan(data)
            .map_err(|_| InspectionFailure("scanner error or timeout"))?;
        let matched: Vec<String> = results
            .matching_rules()
            .take(MAX_SCAN_MATCHES)
            .map(|rule| rule.identifier().to_string())
            .collect();
        if matched.is_empty() {
            Ok(Verdict::NoMatch)
        } else {
            Ok(Verdict::Match(matched))
        }
    }

    /// How many scans this response has had. The pipeline asserts on this so a
    /// refactor cannot quietly drop a required pass.
    pub fn scans(&self) -> usize {
        self.scans
    }

    pub fn remaining_bytes(&self) -> usize {
        self.remaining_bytes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle_path() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("workspace root")
            .join("rules/web-content-v1")
    }

    #[test]
    fn the_shipped_bundle_compiles_and_identifies_itself() {
        let bundle = RuleBundle::load(&bundle_path()).expect("bundle loads");
        assert_eq!(bundle.policy_id(), "web-content-v1");
        assert_eq!(bundle.policy_digest().len(), 64);
        assert!(bundle.rule_count() >= 4, "the bundle lost rules");
    }

    #[test]
    fn an_absent_or_empty_bundle_does_not_load() {
        let directory = std::env::temp_dir().join(format!("webfetch-rules-{}", std::process::id()));
        fs::create_dir_all(&directory).expect("temp dir");
        assert!(
            RuleBundle::load(&directory).is_err(),
            "a bundle with no manifest must not load"
        );
        let manifest = format!(
            r#"{{"policy_version":1,"policy_id":"{}","description":"d","maintenance":"m"}}"#,
            directory
                .file_name()
                .and_then(|name| name.to_str())
                .expect("name")
        );
        fs::write(directory.join("bundle.json"), manifest).expect("write");
        assert!(
            RuleBundle::load(&directory).is_err(),
            "a bundle with no rules must not load"
        );
        fs::write(
            directory.join("broken.yar"),
            b"rule nope { condition: this is not yara }",
        )
        .expect("write");
        assert!(
            RuleBundle::load(&directory).is_err(),
            "a bundle that does not compile must not load"
        );
        fs::remove_dir_all(&directory).ok();
    }

    #[test]
    fn the_scan_budget_is_spent_rather_than_stretched() {
        let bundle = RuleBundle::load(&bundle_path()).expect("bundle loads");
        let mut detector = bundle.detector();
        let big = vec![b'a'; MAX_SCAN_INPUT_BYTES];
        assert_eq!(detector.scan(&big), Ok(Verdict::NoMatch));
        assert_eq!(detector.remaining_bytes(), 0);
        assert!(
            detector.scan(b"one more byte").is_err(),
            "an exhausted budget must fail, not pass"
        );
    }
}

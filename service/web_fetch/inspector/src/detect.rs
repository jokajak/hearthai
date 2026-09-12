//! The detection gate.
//!
//! Three outcomes and no fourth: the rules did not match, the rules matched, or
//! the scan did not complete. There is no score, no vote, no warn-and-return,
//! and no way for a caller to ask for a weaker rule set. A MATCH rejects the
//! whole response; an ERROR withholds it. A NO_MATCH means the configured rules
//! did not fire - not that the content is safe.

use std::time::{Duration, Instant};

use yara_x::{Compiler, Rules, Scanner};

/// Detector limits. Compilation and scanning both get ceilings, because a rule
/// bundle is operator-supplied and a body is not supplied by anyone we trust.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DetectorLimits {
    pub max_rule_files: usize,
    pub max_rule_bytes: usize,
    pub max_scan_bytes: usize,
    pub max_matches_per_pattern: usize,
    pub scan_timeout: Duration,
    /// One budget for every scan in a run, including each converter candidate.
    pub total_scan_input_bytes: usize,
    pub total_inspection_time: Duration,
}

impl Default for DetectorLimits {
    fn default() -> Self {
        Self {
            max_rule_files: 32,
            max_rule_bytes: 256 * 1024,
            max_scan_bytes: 1024 * 1024,
            max_matches_per_pattern: 64,
            scan_timeout: Duration::from_secs(5),
            total_scan_input_bytes: 4 * 1024 * 1024,
            total_inspection_time: Duration::from_secs(5),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    NoMatch,
    Match,
    Error,
}

pub trait Detector {
    fn scan(&self, bytes: &[u8]) -> Verdict;
    fn policy_id(&self) -> &str;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BundleError {
    /// No bundle at all. With no rules, the tool is unavailable.
    Missing,
    /// The manifest or a rule file is unreadable or does not parse.
    Malformed,
    /// The bundle is larger, or has more files, than the limits allow.
    TooLarge,
    /// The rules do not compile, or use syntax outside the supported subset.
    Invalid,
}

/// A validated set of rule sources, before compilation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuleBundle {
    pub policy_id: String,
    pub sources: Vec<(String, String)>,
}

impl RuleBundle {
    /// Loads `bundle.json` plus the `.yar` files it names, from a read-only
    /// mount. There is deliberately no path here that can be reached from a
    /// fetched response: rules come from the deployment or not at all.
    pub fn load(directory: &std::path::Path, limits: &DetectorLimits) -> Result<Self, BundleError> {
        let manifest =
            std::fs::read_to_string(directory.join("bundle.json")).map_err(|_| BundleError::Missing)?;
        let value: serde_json::Value = serde_json::from_str(&manifest).map_err(|_| BundleError::Malformed)?;
        let object = value.as_object().ok_or(BundleError::Malformed)?;
        let policy_id = object.get("policy_id").and_then(|v| v.as_str()).ok_or(BundleError::Malformed)?;
        let files = object.get("rules").and_then(|v| v.as_array()).ok_or(BundleError::Malformed)?;
        if files.is_empty() {
            return Err(BundleError::Malformed);
        }
        if files.len() > limits.max_rule_files {
            return Err(BundleError::TooLarge);
        }

        let mut sources = Vec::new();
        let mut total = 0usize;
        for entry in files {
            let name = entry.as_str().ok_or(BundleError::Malformed)?;
            // Rule names are simple filenames: no traversal out of the mount.
            if !name.ends_with(".yar") || name.contains('/') || name.contains("..") {
                return Err(BundleError::Malformed);
            }
            let source = std::fs::read_to_string(directory.join(name)).map_err(|_| BundleError::Malformed)?;
            total += source.len();
            if total > limits.max_rule_bytes {
                return Err(BundleError::TooLarge);
            }
            sources.push((name.to_owned(), source));
        }
        Ok(Self { policy_id: policy_id.to_owned(), sources })
    }
}

impl std::fmt::Debug for YaraDetector {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Never the rules themselves: a debug line is not a place to print the
        // patterns, and the policy identifier is what an operator needs.
        f.debug_struct("YaraDetector").field("policy_id", &self.policy_id).finish()
    }
}

pub struct YaraDetector {
    rules: Rules,
    policy_id: String,
    limits: DetectorLimits,
}

impl YaraDetector {
    pub fn compile(bundle: &RuleBundle, limits: DetectorLimits) -> Result<Self, BundleError> {
        let mut compiler = Compiler::new();
        // Includes would let one rule file pull in a path the bundle never
        // declared, and modules are outside the supported subset in v1.
        compiler.enable_includes(false);
        compiler.error_on_slow_pattern(true);
        compiler.error_on_slow_loop(true);
        for module in ["pe", "elf", "macho", "dotnet", "lnk", "magic", "math", "hash", "time", "console"] {
            compiler.ban_module(module, "unsupported_module", "modules are not part of the supported subset");
        }
        for (name, source) in &bundle.sources {
            let source = yara_x::SourceCode::from(source.as_str()).with_origin(name);
            if compiler.add_source(source).is_err() {
                return Err(BundleError::Invalid);
            }
        }
        if !compiler.errors().is_empty() {
            return Err(BundleError::Invalid);
        }
        Ok(Self { rules: compiler.build(), policy_id: bundle.policy_id.clone(), limits })
    }

    pub fn rule_count(&self) -> usize {
        self.rules.iter().count()
    }
}

impl Detector for YaraDetector {
    fn scan(&self, bytes: &[u8]) -> Verdict {
        if bytes.len() > self.limits.max_scan_bytes {
            // Refusing to scan is not the same as scanning and finding nothing.
            return Verdict::Error;
        }
        let mut scanner = Scanner::new(&self.rules);
        scanner.set_timeout(self.limits.scan_timeout);
        scanner.max_matches_per_pattern(self.limits.max_matches_per_pattern);
        scanner.max_scan_size(self.limits.max_scan_bytes);
        match scanner.scan(bytes) {
            Ok(results) if results.matching_rules().len() == 0 => Verdict::NoMatch,
            Ok(_) => Verdict::Match,
            // A timeout, an aborted scan, or anything else the engine reports
            // leaves us without a complete answer, so there is no answer.
            Err(_) => Verdict::Error,
        }
    }

    fn policy_id(&self) -> &str {
        &self.policy_id
    }
}

/// Keeps the previously valid bundle in place when an update fails.
///
/// A deployment that pushes a broken rule file should keep the rules it had,
/// not lose detection and keep serving.
pub struct ActiveBundle {
    detector: Box<dyn Detector + Send + Sync>,
}

impl ActiveBundle {
    pub fn new(detector: Box<dyn Detector + Send + Sync>) -> Self {
        Self { detector }
    }

    pub fn detector(&self) -> &dyn Detector {
        self.detector.as_ref()
    }

    pub fn replace(
        &mut self,
        candidate: Result<Box<dyn Detector + Send + Sync>, BundleError>,
    ) -> Result<(), BundleError> {
        self.detector = candidate?;
        Ok(())
    }
}

/// Tracks the shared budget across every scan in one run.
///
/// The fallback chain gets one deadline and one byte budget between all of its
/// attempts, so a page cannot buy extra scanning by failing conversion twice.
pub struct ScanBudget<'a> {
    detector: &'a dyn Detector,
    limits: DetectorLimits,
    started: Instant,
    spent_bytes: std::cell::Cell<usize>,
}

impl<'a> ScanBudget<'a> {
    pub fn new(detector: &'a dyn Detector, limits: DetectorLimits) -> Self {
        Self { detector, limits, started: Instant::now(), spent_bytes: std::cell::Cell::new(0) }
    }

    pub fn policy_id(&self) -> &str {
        self.detector.policy_id()
    }

    pub fn scan(&self, bytes: &[u8]) -> Verdict {
        let spent = self.spent_bytes.get().saturating_add(bytes.len());
        if spent > self.limits.total_scan_input_bytes
            || self.started.elapsed() > self.limits.total_inspection_time
        {
            return Verdict::Error;
        }
        self.spent_bytes.set(spent);
        self.detector.scan(bytes)
    }

    pub fn spent_bytes(&self) -> usize {
        self.spent_bytes.get()
    }
}

//! Time, with an injectable source and one timestamp format.
//!
//! A fetch's elapsed budget has to be testable without sleeping, and
//! `retrieved_at` has to come out as the RFC 3339 UTC the contract validates.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub trait Clock: Send + Sync {
    fn now(&self) -> SystemTime;
    fn elapsed_since(&self, start: SystemTime) -> Duration {
        self.now().duration_since(start).unwrap_or_default()
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> SystemTime {
        SystemTime::now()
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ`, computed here rather than pulled in as a dependency
/// so the worker image carries one less crate for one format string.
pub fn rfc3339_utc(moment: SystemTime) -> String {
    let seconds = moment.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let (days, remainder) = ((seconds / 86_400) as i64, seconds % 86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        remainder / 3600,
        (remainder % 3600) / 60,
        remainder % 60
    )
}

/// Howard Hinnant's days-from-civil inverse, shifted to a March-based year so
/// the leap day lands at the end and needs no special case.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era = (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let march_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * march_month + 2) / 5 + 1) as u32;
    let month = if march_month < 10 { march_month + 3 } else { march_month - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

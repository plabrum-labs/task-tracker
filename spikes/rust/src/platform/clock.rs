//! The one source of timestamps, so a write can stamp both columns alike.
//!
//! RFC3339 UTC to the second, as text, because that is what SQLite sorts
//! lexicographically and what the Go backend's `TimeMixin` already writes. A
//! write computes [`now`] once and uses the result for `created_at` and
//! `updated_at` both, which is what makes a freshly created row carry one
//! instant in both stamps.

use chrono::{SecondsFormat, Utc};

pub fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

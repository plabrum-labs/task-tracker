use std::fmt;

use crate::store::StoreError;

/// The refusals a caller can get back, mirroring `backend/errs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The request does not make sense.
    Invalid(String),
    /// The request makes sense; the row says no.
    Conflict(String),
    /// Neither: the request was fine and the machine was not. A store failure
    /// has no object to blame, and dressing it up as one of the other two would
    /// put a database error where a reason belongs.
    ///
    /// `action.rs` never produces this — it is the frontends' business, which
    /// is why [`StoreError`] converts into it here and not the other way round.
    Broken(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Invalid(m) => write!(f, "invalid: {m}"),
            Error::Conflict(m) => write!(f, "conflict: {m}"),
            Error::Broken(m) => write!(f, "error: {m}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<StoreError> for Error {
    fn from(e: StoreError) -> Self {
        Error::Broken(e.to_string())
    }
}

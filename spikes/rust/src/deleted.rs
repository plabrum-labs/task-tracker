//! A row in the trash: the domain object it was, and when it went.
//!
//! One generic type where the OCaml spike writes `Issue.deleted` and
//! `Project.deleted` as two records. `Group<Deleted<Issue>>` and
//! `Group<Deleted<Project>>` are then distinct types for free, which is what
//! makes "a deleted row cannot be edited" a fact about what compiles rather
//! than a rule the registration lists happen to follow.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Deleted<T> {
    pub inner: T,
    pub deleted_at: String,
}

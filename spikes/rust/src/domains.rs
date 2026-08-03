//! One directory per persisted object, and the schema that declares both.
//!
//! `schema.rs` is the one file that names the base tables, shared here rather
//! than hidden per-domain because sea-orm's typed relations couple the two
//! entities — an issue `belongs_to` a project, so the pair cannot be split. It
//! is `pub(in crate::domains)`, so each domain's `services.rs` can name its own
//! table and `frontend/` cannot name any. Each `issue` and `project` module is
//! the domain object, its payload shapes, its queries and its actions.

pub mod schema;

pub mod issue;
pub mod project;

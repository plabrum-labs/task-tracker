(** The issue domain, as one module.

    A file named after its own directory is the group's interface: dune makes it the only module
    reachable from outside, and the siblings it re-exports become unreachable except through it.
    That is what lets the rest of the tree keep writing {!Issue.t} while the directory keeps its
    files named for what they hold.

    It is also a constraint worth knowing about before moving anything here: the siblings cannot
    depend on this file, only the other way round. [services.ml] refers to the model as [Models],
    never as [Issue]. *)

include Models

module Services = Services
(** The issues table, and the only module that names it. *)

module Schemas = Schemas
(** The payload shapes an issue's actions accept. *)

module Actions = Actions
(** Everything an issue can be asked, in two registrations. *)

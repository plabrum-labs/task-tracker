(** The project domain, as one module.

    Named after its own directory, so dune makes it the group's interface — see [../issue/issue.ml]
    for what that costs and what it buys. *)

include Models

module Services = Services
(** The projects table, and the only module that names it. *)

module Schemas = Schemas
(** The payload shapes a project's actions accept. *)

module Actions = Actions
(** Everything a project can be asked, and the two things that make one. *)

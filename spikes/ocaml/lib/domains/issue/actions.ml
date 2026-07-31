(** Everything an issue can be asked, in two registrations.

    One action is one {!Wire.Make}: its key, its two hooks, its payload type and — from that type —
    both the decoder and the advertised schema. Its [execute] is given the open transaction and
    writes for itself: an edit is an UPDATE, a delete stamps [deleted_at], and nothing outside the
    body says which. Nothing anywhere states any part of it a second time.

    The [Spec] each application is given is named rather than anonymous, and that is the one thing
    here that is not free. An anonymous structure has no path, so its record labels are unreachable
    and nothing outside could write [Action.run issue Edit_title.action { title = "x" }] — the typed
    path would compile and be unusable. Naming it costs two lines per action and is the whole of
    what the functor form did not remove.

    There is one group per object. {!group} is everything a live issue offers — the four edits and
    the delete, which is an update like any other now that [execute] does its own write. The only
    split left is by the object itself: {!deleted_group} is offered against a row in the trash, so a
    deleted issue cannot be edited because an edit was never registered against its type.

    None of the four edits refuses anything. Many issues may be [doing] at once, there is no WIP
    rule, and nothing else about an issue constrains what may be done to it — so both hooks are the
    default in every case and the refusals are all [execute]'s. *)

open Platform
open Ppx_yojson_conv_lib.Yojson_conv.Primitives

(** The editable columns, written and reported as one. [updated_at] is stamped by the store, so
    [execute] stays a function of the object and its payload. *)
let saved (issue : Models.t) conn =
  Db.broken (Services.update issue conn) |> Result.map (fun () -> Models.subject issue ^ ": saved")

module Edit_title = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t

    type payload = { title : string  (** What to call the issue. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editTitle"

    (* The annotation is load-bearing: [payload] is declared here and also has a
       [title] field, so without it type-directed record disambiguation reads
       [{ issue with title }] as a payload and the error surfaces at the
       functor application instead. *)
    let execute (issue : obj) p conn =
      let title = String.trim p.title in
      if title = "" then Error (Error.Invalid "title is required")
      else saved { issue with title } conn
  end

  include Wire.Make (Spec)
end

module Edit_body = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t

    type payload = { body : string  (** What the issue is about. Blank clears it. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editBody"

    (* Same payload shape as [editTitle] and a different rule: a blank body is
       how you clear one. Two actions the schema cannot tell apart, which is
       where an action still earns its keep over a generated form. *)
    let execute (issue : obj) p conn = saved { issue with body = p.body } conn
  end

  include Wire.Make (Spec)
end

module Edit_status = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t

    type payload = {
      status : Models.Status.t;  (** Where the issue is up to. *)
      note : string option; [@yojson.option] [@jsonschema.option]
          (** Why it moved. Left out, whatever described the old status goes with it. *)
    }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editStatus"

    (* The note describes the status it arrived with, so moving without one
       clears the old note rather than leaving it to describe a state the issue
       is no longer in. *)
    let execute (issue : obj) p conn =
      saved { issue with status = p.status; status_note = p.note } conn
  end

  include Wire.Make (Spec)
end

module Edit_priority = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t

    type payload = { priority : Models.Priority.t  (** How far up the list it sorts. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editPriority"
    let execute (issue : obj) p conn = saved { issue with priority = p.priority } conn
  end

  include Wire.Make (Spec)
end

module Delete = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Models.t

    let key = "delete"

    (* The soft delete, which is an update like any other — the column it sets is
       not on {!Models.t} at all, so the whole of the write is the store call. *)
    let execute issue () conn =
      Db.broken (Services.delete issue conn)
      |> Result.map (fun () -> Models.subject issue ^ ": deleted")
  end

  include Wire.Make (Spec)
end

module Restore = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Models.t Deleted.t

    let key = "restore"

    let execute (deleted : obj) () conn =
      Db.broken (Services.restore deleted conn)
      |> Result.map (fun () -> Models.subject deleted.inner ^ ": restored")
  end

  include Wire.Make (Spec)
end

(** Everything a live issue offers, in the order it is offered. *)
let group : Models.t Wire.group =
  [ Edit_title.entry; Edit_body.entry; Edit_status.entry; Edit_priority.entry; Delete.entry ]

(** What a row in the trash offers, which is coming back and nothing else. Offered against the
    deleted type, so an edit cannot reach it. *)
let deleted_group : Models.t Deleted.t Wire.group = [ Restore.entry ]

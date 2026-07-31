(** Everything an issue can be asked, in three registrations.

    One action is one {!Wire.Make}: its key, its two hooks, its payload type and — from that type —
    both the decoder and the advertised schema. Nothing anywhere states any part of it a second
    time, and there is no pairing at the registration site left to get wrong.

    The [Spec] each application is given is named rather than anonymous, and that is the one thing
    here that is not free. An anonymous structure has no path, so its record labels are unreachable
    and nothing outside could write [Action.run issue Edit_title.action { title = "x" }] — the typed
    path would compile and be unusable. Naming it costs two lines per action and is the whole of
    what the functor form did not remove.

    The split into three is by what the store does with the result, not by what the action means.
    {!group} is written by an UPDATE of the editable columns, {!trash} by one that stamps
    [deleted_at], {!deleted_group} by one that clears it — and all three have the same shape,
    because a soft delete is an update. Each group names its store call once, here, which is the
    only thing that keeps a delete from being persisted as an edit.

    None of the four edits refuses anything. Many issues may be [doing] at once, there is no WIP
    rule, and nothing else about an issue constrains what may be done to it — so both hooks are the
    default in every case and the refusals are all [execute]'s. *)

open Platform
open Ppx_yojson_conv_lib.Yojson_conv.Primitives

module Edit_title = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t
    type out = Models.t

    type payload = { title : string  (** What to call the issue. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editTitle"

    (* The annotation is load-bearing: [payload] is declared here and also has a
       [title] field, so without it type-directed record disambiguation reads
       [{ issue with title }] as a payload and the error surfaces at the
       functor application instead. *)
    let execute (issue : obj) p =
      let title = String.trim p.title in
      if title = "" then Error (Error.Invalid "title is required") else Ok { issue with title }
  end

  include Wire.Make (Spec)
end

module Edit_body = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t
    type out = Models.t

    type payload = { body : string  (** What the issue is about. Blank clears it. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editBody"

    (* Same payload shape as [editTitle] and a different rule: a blank body is
       how you clear one. Two actions the schema cannot tell apart, which is
       where an action still earns its keep over a generated form. *)
    let execute (issue : obj) p = Ok { issue with body = p.body }
  end

  include Wire.Make (Spec)
end

module Edit_status = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t
    type out = Models.t

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
    let execute (issue : obj) p = Ok { issue with status = p.status; status_note = p.note }
  end

  include Wire.Make (Spec)
end

module Edit_priority = struct
  module Spec = struct
    include Action.Defaults

    type obj = Models.t
    type out = Models.t

    type payload = { priority : Models.Priority.t  (** How far up the list it sorts. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editPriority"
    let execute (issue : obj) p = Ok { issue with priority = p.priority }
  end

  include Wire.Make (Spec)
end

module Delete = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Models.t
    type out = Models.t

    let key = "delete"

    (* The identity. What the write is lives entirely in the group's [persist],
       because the column it sets is not on {!Models.t} at all — a live issue has
       no [deleted_at] to assign. *)
    let execute issue () = Ok issue
  end

  include Wire.Make (Spec)
end

module Restore = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Models.t Deleted.t
    type out = Models.t Deleted.t

    let key = "restore"
    let execute deleted () = Ok deleted
  end

  include Wire.Make (Spec)
end

(** The edits, in the order they are offered. *)
let group : (Models.t, Models.t, Db.conn) Wire.group =
  {
    entries = [ Edit_title.entry; Edit_body.entry; Edit_status.entry; Edit_priority.entry ];
    persist =
      (fun conn issue ->
        Db.broken (Services.update issue conn)
        |> Result.map (fun () -> Models.subject issue ^ ": saved"));
  }

(** Leaving. Separate from {!group} only because the write that follows is. *)
let trash : (Models.t, Models.t, Db.conn) Wire.group =
  {
    entries = [ Delete.entry ];
    persist =
      (fun conn issue ->
        Db.broken (Services.delete issue conn)
        |> Result.map (fun () -> Models.subject issue ^ ": deleted"));
  }

(** What a row in the trash offers, which is coming back and nothing else. *)
let deleted_group : (Models.t Deleted.t, Models.t Deleted.t, Db.conn) Wire.group =
  {
    entries = [ Restore.entry ];
    persist =
      (fun conn deleted ->
        Db.broken (Services.restore deleted conn)
        |> Result.map (fun () -> Models.subject deleted.inner ^ ": restored"));
  }

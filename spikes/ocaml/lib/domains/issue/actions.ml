(** Everything an issue can be asked, in two registrations.

    One action is one [%action …]: its object type, its key, its two hooks and its [execute], which
    holds the open transaction and writes for itself — an edit is an UPDATE, a delete stamps
    [deleted_at], and nothing outside the body says which. The ppx wraps each into the
    [module Spec = struct … end] / [include Wire.Make (Spec)] pair the reader no longer writes, and
    supplies the typed [action] and the erased [entry] a group is built from.

    The payload shapes are in {!Schemas}, one type per action, so an action names only
    [type payload = Schemas.<name>] and states nothing about how that type is decoded. An
    argumentless action names no payload and [include]s {!Wire.No_payload} instead.

    There is one group per object. {!group} is everything a live issue offers — the four edits and
    the delete. The only split left is by the object itself: {!deleted_group} is offered against a
    row in the trash, so a deleted issue cannot be edited because an edit was never registered
    against its type.

    None of the four edits refuses anything. Many issues may be [doing] at once, there is no WIP
    rule, and nothing else about an issue constrains what may be done to it — so both hooks are the
    default in every case and the refusals are all [execute]'s. *)

open Platform

(** The editable columns, written and reported as one. [updated_at] is stamped by the store, so
    [execute] stays a function of the object and its payload. *)
let saved (issue : Models.t) conn =
  Db.broken (Services.update issue conn) |> Result.map (fun () -> Models.subject issue ^ ": saved")

module Edit_title =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_title

  let key = "editTitle"

  (* [(p : payload)] pins [p.title] to {!Schemas.edit_title}, and [(issue : obj)] pins
   [{ issue with title }] to {!Models.t}; without them, [title] is ambiguous. *)
  let execute (issue : obj) (p : payload) conn =
    let title = String.trim p.title in
    if title = "" then Error (Error.Invalid "title is required")
    else saved { issue with title } conn]

module Edit_body =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_body

  let key = "editBody"

  (* Same payload shape as [editTitle] and a different rule: a blank body is how you clear one. Two
   actions the schema cannot tell apart, which is where an action still earns its keep over a
   generated form. *)
  let execute (issue : obj) (p : payload) conn = saved { issue with body = p.body } conn]

module Edit_status =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_status

  let key = "editStatus"

  (* The note describes the status it arrived with, so moving without one clears the old note rather
   than leaving it to describe a state the issue is no longer in. *)
  let execute (issue : obj) (p : payload) conn =
    saved { issue with status = p.status; status_note = p.note } conn]

module Edit_priority =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_priority

  let key = "editPriority"
  let execute (issue : obj) (p : payload) conn = saved { issue with priority = p.priority } conn]

module Delete =
  [%action
  include Action.Defaults
  include Wire.No_payload

  type obj = Models.t

  let key = "delete"

  (* The soft delete, which is an update like any other — the column it sets is not on {!Models.t} at
   all, so the whole of the write is the store call. *)
  let execute issue () conn =
    Db.broken (Services.delete issue conn)
    |> Result.map (fun () -> Models.subject issue ^ ": deleted")]

module Restore =
  [%action
  include Action.Defaults
  include Wire.No_payload

  type obj = Models.t Deleted.t

  let key = "restore"

  let execute (deleted : obj) () conn =
    Db.broken (Services.restore deleted conn)
    |> Result.map (fun () -> Models.subject deleted.inner ^ ": restored")]

(** Everything a live issue offers, in the order it is offered. *)
let group : Models.t Wire.group =
  [ Edit_title.entry; Edit_body.entry; Edit_status.entry; Edit_priority.entry; Delete.entry ]

(** What a row in the trash offers, which is coming back and nothing else. Offered against the
    deleted type, so an edit cannot reach it. *)
let deleted_group : Models.t Deleted.t Wire.group = [ Restore.entry ]

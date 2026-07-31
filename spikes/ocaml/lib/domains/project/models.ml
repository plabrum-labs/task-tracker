(** A project, and the issues under it counted.

    The counts are part of the projection rather than something a caller fetches when it needs them,
    because [editStatus] and [delete] both refuse on them. An availability hook is a pure function
    of the object, so anything a hook reads has to be in the object — computing the counts per row
    is what stops a list from offering a menu it cannot justify. *)

open Platform

(* The same three declarations as {!Issue.Status}, and the same reason they are
   not derived. See issue.ml. *)
module Status = struct
  type t = Active | Archived

  let to_string = function Active -> "active" | Archived -> "archived"
  let of_string = function "active" -> Some Active | "archived" -> Some Archived | _ -> None
  let names = [ "active"; "archived" ]
  let yojson_of_t value = `String (to_string value)
  let t_of_yojson = Wire.enum_of_yojson ~name:"status" ~of_string ~names
  let t_jsonschema = Wire.enum_jsonschema ~names
end

type t = {
  id : int;
  slug : string;
  title : string;
  body : string;
  status : Status.t;
  todo : int;
  doing : int;
  done_ : int;  (** [done] is a keyword *)
  created_at : string;
  updated_at : string;
}

type draft = { slug : string; title : string; body : string }

type restorable = { deleted : t Deleted.t; slug_taken : bool }
(** What [restore] is offered against.

    The trash row alone is not enough: the partial unique index covers live rows only, so a slug
    freed by a delete can be taken again and bringing the old project back would then collide. An
    availability hook is a pure function of its object, so what the hook reads has to be in the
    object — the same reason {!t} carries its counts, and the same reason [createProject]'s parent
    is the list rather than nothing.

    It is the answer and not the evidence. Carrying the whole live project list to compute one
    boolean would put a list on screen in every trash row, and the hook is the only thing that ever
    reads it. Without the boolean the refusal is a UNIQUE constraint violation surfacing as a
    database error with no sentence in it. *)

(* Annotated because {!draft} is declared later and shares field names: without
   it, type-directed record disambiguation reads [t.slug] as a draft's and
   [subject] silently becomes a function over the wrong type, with the error
   surfacing at some caller instead. *)
let subject (t : t) = Printf.sprintf "project %s" t.slug
let issue_count (t : t) = t.todo + t.doing + t.done_

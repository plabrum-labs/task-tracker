(** The refusals a caller can get back, mirroring [backend/errs]. *)

type t =
  | Invalid of string  (** the request does not make sense *)
  | Conflict of string  (** the request makes sense, the row says no *)
  | Broken of string
      (** neither: the request was fine and the machine was not. A store failure has no object to
          blame, and dressing it up as one of the other two would put a database error where a
          reason belongs. *)

let to_string = function
  | Invalid m -> "invalid: " ^ m
  | Conflict m -> "conflict: " ^ m
  | Broken m -> "error: " ^ m

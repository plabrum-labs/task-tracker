type ('obj, 'p, 'conn) t = {
  key : string;
  is_available : 'obj -> bool;
  is_disabled : 'obj -> string option;
  execute : 'obj -> 'p -> 'conn -> (string, Error.t) result;
}

module Defaults = struct
  let is_available _ = true
  let is_disabled _ = None
end

let make ~key ~is_available ~is_disabled ~execute = { key; is_available; is_disabled; execute }
let key action = action.key
let is_available action obj = action.is_available obj
let is_disabled action obj = action.is_disabled obj

let run obj action payload conn =
  if not (action.is_available obj) then
    Error (Error.Conflict (Printf.sprintf "%s does not apply" action.key))
  else
    match action.is_disabled obj with
    | Some reason -> Error (Error.Conflict reason)
    | None -> action.execute obj payload conn

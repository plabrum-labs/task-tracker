(** The one source of timestamps, so a write can stamp both columns alike.

    RFC3339 UTC to the second, as text, because that is what SQLite sorts lexicographically and what
    the Go backend's [TimeMixin] already writes. A write computes {!now} once and uses the result
    for [created_at] and [updated_at] both, which is what makes a freshly created row carry one
    instant in both stamps. *)

let now () =
  let t = Unix.gmtime (Unix.gettimeofday ()) in
  Printf.sprintf "%04d-%02d-%02dT%02d:%02d:%02dZ" (t.tm_year + 1900) (t.tm_mon + 1) t.tm_mday
    t.tm_hour t.tm_min t.tm_sec

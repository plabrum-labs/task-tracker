(** Everything, in three suites: the domain and the wire, the SQL edge, and the two frontends. One
    binary, because they are one application. *)

let () = Alcotest.run "tt" (Test_actions.suite @ Test_store.suite @ Test_frontend.suite)

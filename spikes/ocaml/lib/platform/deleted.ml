(** A row in the trash: the domain object it was, and when it went.

    One parameterised type where an earlier round wrote [Issue.deleted] and [Project.deleted] as two
    records of the same shape. [Issue.t Deleted.t] and [Project.t Deleted.t] are distinct types for
    free, which is what makes "a deleted row cannot be edited" a fact about what compiles rather
    than a rule the registration lists happen to follow. *)

type 'a t = { inner : 'a; deleted_at : string }

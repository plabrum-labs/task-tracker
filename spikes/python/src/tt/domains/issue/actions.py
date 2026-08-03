"""Everything an issue can be asked, in two registrations.

One action is one class: its object type, its key, its two hooks and its
``execute``, which holds the open transaction and writes for itself — an edit is
an ``UPDATE``, a delete stamps ``deleted_at``, and nothing outside the body says
which. The payload shapes are in ``schemas``, one model per action.

There is one group per object. ``group`` is everything a live issue offers — the
four edits and the delete. ``deleted_group`` is offered against ``Deleted[Issue]``,
so a deleted issue cannot be edited because an edit was never registered against
its type.

None of the four edits refuses anything. Many issues may be ``doing`` at once,
there is no WIP rule, so both hooks are the default in every case and the refusals
are all ``execute``'s.
"""

from dataclasses import replace

from sqlalchemy.orm import Session

from tt.domains.issue import Issue, schemas, services
from tt.platform.action import Action, Checked
from tt.platform.deleted import Deleted
from tt.platform.error import Invalid
from tt.platform.wire import Empty, Group, entry_of


def _saved(issue: Issue, tx: Session) -> str:
    """The editable columns, written and reported as one. ``updated_at`` is
    stamped by the store, so an ``execute`` states only what it changed."""
    services.update_issue(tx, issue)
    return f"{issue.subject()}: saved"


class EditTitle(Action[Issue, schemas.EditTitlePayload]):
    KEY = "editTitle"
    Payload = schemas.EditTitlePayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditTitlePayload, tx: Session, checked: Checked
    ) -> str:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        return _saved(replace(obj, title=title), tx)


class EditBody(Action[Issue, schemas.EditBodyPayload]):
    # The same payload shape as ``editTitle`` and a different rule: a blank body is
    # how you clear one. Two actions the schema cannot tell apart.
    KEY = "editBody"
    Payload = schemas.EditBodyPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditBodyPayload, tx: Session, checked: Checked
    ) -> str:
        return _saved(replace(obj, body=payload.body), tx)


class EditStatus(Action[Issue, schemas.EditStatusPayload]):
    # The note describes the status it arrived with, so moving without one clears
    # the old note rather than leaving it to describe a state the issue is no
    # longer in.
    KEY = "editStatus"
    Payload = schemas.EditStatusPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditStatusPayload, tx: Session, checked: Checked
    ) -> str:
        return _saved(replace(obj, status=payload.status, status_note=payload.note), tx)


class EditPriority(Action[Issue, schemas.EditPriorityPayload]):
    KEY = "editPriority"
    Payload = schemas.EditPriorityPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditPriorityPayload, tx: Session, checked: Checked
    ) -> str:
        return _saved(replace(obj, priority=payload.priority), tx)


class Delete(Action[Issue, Empty]):
    # The soft delete, which is an update like any other — the column it sets is
    # not on ``Issue`` at all, so the whole of the write is the store call.
    KEY = "delete"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Issue, payload: Empty, tx: Session, checked: Checked) -> str:
        subject = obj.subject()
        services.delete_issue(tx, obj)
        return f"{subject}: deleted"


class Restore(Action[Deleted[Issue], Empty]):
    KEY = "restore"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Deleted[Issue], payload: Empty, tx: Session, checked: Checked) -> str:
        services.restore_issue(tx, obj)
        return f"{obj.inner.subject()}: restored"


def group() -> Group[Issue]:
    """Everything a live issue offers, in the order it is offered."""
    return [
        entry_of(EditTitle),
        entry_of(EditBody),
        entry_of(EditStatus),
        entry_of(EditPriority),
        entry_of(Delete),
    ]


def deleted_group() -> Group[Deleted[Issue]]:
    """What a row in the trash offers, which is coming back and nothing else.

    ``Deleted[Issue]`` rather than ``Issue`` is what makes "a deleted issue cannot
    be edited" a fact about what typechecks: ``group`` does not apply to one."""
    return [entry_of(Restore)]

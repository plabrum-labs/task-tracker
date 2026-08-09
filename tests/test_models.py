"""The pure helpers on the issue models, tested without a database.

``status_counts`` is a pure function of an iterable of issues, so it is exercised
directly on in-memory rows rather than through the store — the testing guide's
rule for functions that need no session.
"""

import pytest

from tt.domains.issue import Status
from tt.domains.issue.models import Issue, status_counts


def _issue(status: Status) -> Issue:
    return Issue(status=status)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        # Nothing under the container still names every status, at zero.
        ([], {status: 0 for status in Status}),
        (
            [Status.DOING, Status.DOING, Status.DONE, Status.BACKLOG],
            {
                Status.BACKLOG: 1,
                Status.BLOCKED: 0,
                Status.TODO: 0,
                Status.DOING: 2,
                Status.DONE: 1,
                Status.CANCELED: 0,
            },
        ),
    ],
)
def test_status_counts_is_dense(statuses: list[Status], expected: dict[Status, int]) -> None:
    assert status_counts(_issue(status) for status in statuses) == expected

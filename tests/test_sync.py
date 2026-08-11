"""The pure heart of sync: which mirror a pull chooses, and the schedule strings the
installer writes.

The ssh/rsync/launchctl/crontab calls are the untested edge; everything here is a
function of its inputs — ``choose_source`` over resolved mtimes, and the plist/cron
builders over a path and an interval — so no database or network is needed.
"""

import pytest

from tt.platform.config import Mirror
from tt.platform.sync import (
    choose_source,
    cron_block,
    cron_line,
    cron_minutes,
    cron_with_block,
    cron_without_block,
    plist,
)

_WALTER = Mirror(host="walter")
_HOUSTON = Mirror(host="houston")


# --- choose_source --------------------------------------------------------


def test_no_mirror_beats_local_is_none() -> None:
    # Local is the newest, so there is nothing to pull.
    assert choose_source(100.0, [(_WALTER, 50.0), (_HOUSTON, 90.0)]) is None


def test_a_mirror_strictly_newer_than_local_is_chosen() -> None:
    assert choose_source(100.0, [(_WALTER, 150.0)]) == _WALTER


def test_the_newest_mirror_wins() -> None:
    assert choose_source(0.0, [(_WALTER, 100.0), (_HOUSTON, 200.0)]) == _HOUSTON


def test_a_tie_with_local_does_not_pull() -> None:
    # Equal mtimes are already current — a tie is not strictly newer.
    assert choose_source(100.0, [(_WALTER, 100.0)]) is None


def test_a_tie_between_mirrors_takes_the_first() -> None:
    assert choose_source(0.0, [(_WALTER, 100.0), (_HOUSTON, 100.0)]) == _WALTER


def test_no_candidates_is_none() -> None:
    assert choose_source(0.0, []) is None


# --- cron builders --------------------------------------------------------


@pytest.mark.parametrize(
    ("interval", "minutes"),
    [
        (900, 15),
        (60, 1),
        (3600, 60),
        (90, 1),
        # Cron cannot do sub-minute, so anything under 60s floors up to one minute.
        (30, 1),
        (0, 1),
    ],
)
def test_cron_minutes_floors_to_whole_minutes(interval: int, minutes: int) -> None:
    assert cron_minutes(interval) == minutes


def test_cron_line_runs_sync_run_on_the_cadence() -> None:
    assert cron_line("/usr/local/bin/tt", 900) == "*/15 * * * * /usr/local/bin/tt sync run"


def test_cron_block_is_marked_for_removal() -> None:
    block = cron_block("/bin/tt", 900)
    assert block == (
        "# >>> tt sync (managed) >>>\n*/15 * * * * /bin/tt sync run\n# <<< tt sync (managed) <<<"
    )


def test_installing_into_an_empty_crontab() -> None:
    assert cron_with_block("", "/bin/tt", 900) == (
        "# >>> tt sync (managed) >>>\n*/15 * * * * /bin/tt sync run\n# <<< tt sync (managed) <<<\n"
    )


def test_installing_appends_below_a_user_entry() -> None:
    existing = "0 9 * * * /bin/backup\n"
    result = cron_with_block(existing, "/bin/tt", 900)
    assert result == (
        "0 9 * * * /bin/backup\n"
        "# >>> tt sync (managed) >>>\n*/15 * * * * /bin/tt sync run\n# <<< tt sync (managed) <<<\n"
    )


def test_reinstalling_replaces_the_old_block_rather_than_stacking() -> None:
    once = cron_with_block("0 9 * * * /bin/backup\n", "/bin/tt", 900)
    twice = cron_with_block(once, "/bin/tt", 300)
    assert twice.count("# >>> tt sync (managed) >>>") == 1
    assert "*/5 * * * * /bin/tt sync run" in twice
    assert "*/15 * * * *" not in twice
    # The unmanaged entry is untouched by the rewrite.
    assert twice.startswith("0 9 * * * /bin/backup\n")


def test_uninstalling_removes_only_the_managed_block() -> None:
    installed = cron_with_block("0 9 * * * /bin/backup\n", "/bin/tt", 900)
    assert cron_without_block(installed) == "0 9 * * * /bin/backup\n"


def test_uninstalling_an_absent_block_is_a_no_op() -> None:
    assert cron_without_block("0 9 * * * /bin/backup\n") == "0 9 * * * /bin/backup\n"


def test_uninstalling_empties_a_crontab_that_held_only_the_block() -> None:
    installed = cron_with_block("", "/bin/tt", 900)
    assert cron_without_block(installed) == ""


# --- plist builder --------------------------------------------------------


def test_plist_carries_the_interval_and_the_program() -> None:
    written = plist("/usr/local/bin/tt", 900)
    assert "<key>StartInterval</key>\n    <integer>900</integer>" in written
    assert "<string>/usr/local/bin/tt</string>" in written
    assert "<string>sync</string>" in written
    assert "<string>run</string>" in written
    assert "<string>dev.tt.sync</string>" in written
    # RunAtLoad is false, so installing does not fire a pull at once.
    assert "<key>RunAtLoad</key>\n    <false/>" in written


def test_plist_escapes_a_path_with_xml_specials() -> None:
    written = plist("/opt/a & b/tt", 60)
    assert "<string>/opt/a &amp; b/tt</string>" in written

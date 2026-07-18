"""A handler that returned is not the same as work that succeeded.

Live incident (2026-07-18): a production run died in its first seconds on a missing API key.
``GET /jobs/{id}`` reported status "succeeded" while its own result_json read
``{"ok": false, "status": "hard_fail", "summary": "Connection error."}``. The runner had taken
the happy path because the handler RETURNED rather than raised, and nothing ever looked inside
the returned value. The Prometheus counter recorded the dead run as a success too, so no alert
could have fired.

The obvious fix — "treat ok=False as a failed job" — is wrong here, and a blast-radius probe
caught it before it shipped. ``shorts.embed_frames`` returns ``{"ok": False, "skipped": "no
visual backend"}`` as its normal, documented outcome on any install without the optional visual
extra, which is the default for this project. That check would have marked healthy jobs failed
on nearly every machine.

The code already draws the distinction it needs: a graceful skip carries ``skipped`` and no
``error``; a real failure carries ``error``. That is the discriminator, and these tests pin both
sides of it — the failure that must now be recorded, and the skip that must stay a success.
"""

from __future__ import annotations

from laura.jobs.runner import job_failure_from_result

# --- the failures that used to be recorded as successes ------------------------------------


def test_the_incident_result_is_recognised_as_a_failure() -> None:
    """The exact dict from the live run."""
    failure = job_failure_from_result(
        {
            "ok": False,
            "complete": False,
            "status": "hard_fail",
            "stage": "B",
            "summary": "Connection error.",
        }
    )

    assert failure is not None
    assert "Connection error." in failure


def test_a_preflight_error_without_a_status_is_also_a_failure() -> None:
    """run_production's early exits carry no "status" key at all — they were succeeding too."""
    failure = job_failure_from_result({"ok": False, "error": "asset not found"})

    assert failure is not None
    assert "asset not found" in failure


def test_the_message_survives_for_whoever_reads_the_job_row() -> None:
    """A job marked failed with an empty reason is barely better than one marked succeeded."""
    failure = job_failure_from_result({"ok": False, "error": "no succeeded analysis run"})

    assert failure == "no succeeded analysis run"


# --- the graceful skips that must NOT become failures --------------------------------------
# This is the regression the probe caught. Every one of these is a healthy outcome on a default
# install; marking them failed would have been a louder bug than the silent one being fixed.


def test_a_skipped_job_without_a_visual_backend_still_succeeds() -> None:
    """shorts.embed_frames on any install without the optional visual extra."""
    assert job_failure_from_result({"ok": False, "skipped": "no visual backend"}) is None


def test_a_skip_wins_over_ok_being_false() -> None:
    """ok=False is not the signal — the presence of an error is."""
    assert job_failure_from_result({"ok": False, "skipped": "nothing to do", "count": 0}) is None


def test_an_ordinary_successful_result_is_not_a_failure() -> None:
    assert job_failure_from_result({"ok": True, "rows": 12}) is None


def test_a_handler_returning_no_dict_is_not_a_failure() -> None:
    """Plenty of handlers return None or a scalar; the contract must not read into that."""
    assert job_failure_from_result(None) is None
    assert job_failure_from_result("done") is None
    assert job_failure_from_result([1, 2, 3]) is None


def test_an_empty_error_is_not_treated_as_a_failure() -> None:
    """A falsy error key carries no complaint — do not invent one."""
    assert job_failure_from_result({"ok": True, "error": ""}) is None


def test_a_successful_result_that_merely_mentions_a_status_is_left_alone() -> None:
    assert job_failure_from_result({"ok": True, "status": "complete"}) is None

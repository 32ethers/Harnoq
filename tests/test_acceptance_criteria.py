"""Splitting a DESIGN-*'s acceptance_criteria into individual criteria.

How many criteria this finds is how many verdicts the qa-evidence gate demands,
so an under-split blob quietly lets a qa run cover one criterion and call the
design verified.
"""
from app.services import acceptance_criteria


def test_dash_bullets():
    assert acceptance_criteria.split(
        "- Given A, When B, Then C.\n- Given D, When E, Then F."
    ) == ["Given A, When B, Then C.", "Given D, When E, Then F."]


def test_numbered_items():
    assert acceptance_criteria.split("1. First one.\n2. Second one.") == [
        "First one.",
        "Second one.",
    ]


def test_literal_backslash_n_is_repaired_before_splitting():
    """Eight of twelve live designs are stored this way (app.core.agent_text).

    The agent receives the repaired text and quotes criteria split on real
    breaks; parsing the raw column instead would see one criterion where qa saw
    five, and demand a single verdict covering all of them.
    """
    assert acceptance_criteria.split("1. First one.\\n2. Second one.") == [
        "First one.",
        "Second one.",
    ]


def test_wrapped_continuation_lines_stay_with_their_criterion():
    assert acceptance_criteria.split(
        "- Given a long sentence\n  that wrapped in the editor.\n- Given another."
    ) == ["Given a long sentence that wrapped in the editor.", "Given another."]


def test_unmarked_prose_is_one_criterion_per_line():
    assert acceptance_criteria.split("User can log in.\nWrong password returns 401.") == [
        "User can log in.",
        "Wrong password returns 401.",
    ]


def test_empty_input_has_no_criteria():
    assert acceptance_criteria.split("") == []
    assert acceptance_criteria.split("   \n  ") == []
    assert acceptance_criteria.count(None) == 0


def test_normalize_tolerates_whitespace_and_markers_but_not_paraphrase():
    original = "- Given 一个空目录，When 启动，Then 迁移只执行一次。"
    assert acceptance_criteria.normalize(original) == acceptance_criteria.normalize(
        "Given 一个空目录，When   启动，Then 迁移只执行一次。"
    )
    assert acceptance_criteria.normalize(original) != acceptance_criteria.normalize(
        "启动时迁移只跑一次"
    )


def test_normalize_collapses_the_full_width_space():
    assert acceptance_criteria.normalize("Given　A") == "Given A"


def test_heading_blocks_split_per_heading():
    text = (
        "### AC-1 Overdue reminder\n"
        "\n"
        "Given a loan past due,\n"
        "When the nightly job runs,\n"
        "Then a reminder is queued.\n"
        "\n"
        "### AC-2 Duplicate suppression\n"
        "\n"
        "Given a reminder already queued,\n"
        "When the nightly job runs again,\n"
        "Then no second reminder is queued.\n"
    )
    result = acceptance_criteria.split(text)
    assert len(result) == 2
    assert result[0].startswith("AC-1 Overdue reminder")
    assert result[1].startswith("AC-2 Duplicate suppression")


def test_heading_block_criterion_carries_its_given_when_then_body():
    text = (
        "## AC-1 Refund window\n"
        "Given an order placed today,\n"
        "When a refund is requested,\n"
        "Then it is accepted."
    )
    [criterion] = acceptance_criteria.split(text)
    assert "Given an order placed today" in criterion
    assert "When a refund is requested" in criterion
    assert "Then it is accepted." in criterion


def test_heading_blocks_appended_after_a_bulleted_list_are_their_own_criteria():
    """The shape a design grows into: bullets written first, heading blocks
    appended when more criteria are mined later.

    An earlier rule let markers win and dropped the headings, which glued
    every appended body onto the last bullet — one unquotable concatenation,
    and the appended criteria invisible to the qa gate. The criterion COUNT
    was unchanged by that bug, so a count-based check could not see it; these
    assertions are on the text.
    """
    text = (
        "- Given a full cart, When checkout starts, Then totals are shown.\n"
        "- Given an empty cart, When checkout starts, Then it is blocked.\n"
        "\n"
        "### AC-3 Shipping quote\n"
        "\n"
        "Given a valid address,\n"
        "When shipping is chosen,\n"
        "Then a rate is quoted.\n"
    )
    result = acceptance_criteria.split(text)
    assert len(result) == 3
    assert result[1] == "Given an empty cart, When checkout starts, Then it is blocked."
    assert result[2].startswith("AC-3 Shipping quote")
    assert "Then a rate is quoted." in result[2]
    # The appended block must not have been folded into the preceding bullet.
    assert "Shipping" not in result[1]


def test_heading_blocks_with_identical_body_lines_stay_distinct():
    text = (
        "### AC-1 Alpha\n"
        "When the queue is empty, nothing happens.\n"
        "### AC-2 Beta\n"
        "When the queue is empty, nothing happens.\n"
    )
    result = acceptance_criteria.split(text)
    assert len(result) == 2
    assert result[0] != result[1]
    assert result[0].startswith("AC-1 Alpha")
    assert result[1].startswith("AC-2 Beta")


HEADING_BLOB = """\
### AC-1 Refund window

Given an order delivered 20 days ago,
When the buyer requests a refund,
Then the request is accepted.

### AC-2 Queue ordering

Given two jobs enqueued at the same priority,
When the worker pulls,
Then the earlier one runs first.
"""


def test_a_quote_that_keeps_the_heading_marker_still_matches():
    """qa is told to quote each criterion AS WRITTEN, and what it can see in a
    heading-block design includes the "###". split() strips it, so without the
    same strip here every such quote missed by exactly those characters and no
    qa run against a heading-block design could be recorded at all.
    """
    first = acceptance_criteria.split(HEADING_BLOB)[0]
    as_written = (
        "### AC-1 Refund window\n\n"
        "Given an order delivered 20 days ago,\n"
        "When the buyer requests a refund,\n"
        "Then the request is accepted."
    )
    assert acceptance_criteria.normalize(as_written) == acceptance_criteria.normalize(first)
    # And the same quote without the marker lands on the same key.
    assert acceptance_criteria.normalize(
        as_written.replace("### ", "")
    ) == acceptance_criteria.normalize(first)


def test_a_heading_alone_is_not_the_criterion():
    """Tolerating the marker must not tolerate quoting only the title: the
    body is where the promise is, and a title-only quote proves nothing.
    """
    first = acceptance_criteria.split(HEADING_BLOB)[0]
    assert acceptance_criteria.normalize("### AC-1 Refund window") != acceptance_criteria.normalize(first)

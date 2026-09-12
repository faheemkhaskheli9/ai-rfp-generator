import pytest

from ai_rfp_generator.normalize import (
    DEADLINE,
    QUESTION,
    REQUIREMENT,
    SECTION,
    NormalizationError,
    classify_line,
    normalize_text,
)


def test_classifies_question_section_deadline_and_plain_requirement_lines():
    assert classify_line("Can the vendor support SSO?") == QUESTION
    assert classify_line("## Technical Requirements") == SECTION
    assert classify_line("Proposals are due by 2026-10-15.") == DEADLINE
    assert classify_line("The system must support SSO login.") == REQUIREMENT


def test_normalize_text_extracts_ordered_discrete_items_from_a_well_formed_document():
    document = "\n".join(
        [
            "## Technical Requirements",
            "The system must support SSO login.",
            "Does the platform support multi-tenancy?",
            "Submission deadline: September 30, 2026.",
        ]
    )

    items = normalize_text(document)

    assert [item.item_type for item in items] == [SECTION, REQUIREMENT, QUESTION, DEADLINE]
    assert [item.position for item in items] == [0, 1, 2, 3]
    assert items[0].content == "## Technical Requirements"


def test_normalize_text_skips_blank_lines_between_items():
    document = "First requirement line.\n\n\nSecond requirement line."

    items = normalize_text(document)

    assert len(items) == 2
    assert items[1].position == 1


def test_normalize_text_raises_on_whitespace_only_content():
    with pytest.raises(NormalizationError):
        normalize_text("   \n\t\n   ")


def test_normalize_text_raises_on_binary_garbage_content():
    with pytest.raises(NormalizationError):
        normalize_text("some text\x00with a null byte")

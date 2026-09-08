"""The duration filter's contract, independent of the Qt browser.

The expected names in these tests are handwritten from issue #96's policy:
known positive durations use inclusive bounds, while the existing model's
non-positive and malformed duration values are unknown. The assertions do
not call implementation helpers to construct their expected result, so a
filter that agrees with itself cannot make the oracle pass by accident.
"""

from math import inf, nan
from types import SimpleNamespace

import pytest

from flightdvr.clip_filter import ClipFilter


def clip(name: str, duration, review: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, duration=duration, review=review)


def names(clips: list[SimpleNamespace]) -> list[str]:
    return [one.name for one in clips]


def test_minimum_and_maximum_duration_bounds_include_exact_edges():
    clips = [
        clip("below", 9.99),
        clip("minimum", 10.0),
        clip("middle", 20.0),
        clip("maximum", 30.0),
        clip("above", 30.01),
    ]

    assert names(ClipFilter(minimum=10, maximum=30).apply(clips)) == [
        "minimum", "middle", "maximum"]


def test_no_duration_bounds_are_the_reset_that_keeps_unknown_clips():
    clips = [clip("known", 12), clip("zero", 0), clip("missing", None)]

    assert names(ClipFilter().apply(clips)) == names(
        ClipFilter(minimum=None, maximum=None, show_unknown=True).apply(clips)
    ) == ["known", "zero", "missing"]


def test_explicitly_hiding_unknowns_works_without_bounds_and_is_active():
    clips = [clip("known", 12), clip("zero", 0), clip("missing", None)]

    policy = ClipFilter(show_unknown=False)

    assert policy.is_active
    assert names(policy.apply(clips)) == ["known"]


@pytest.mark.parametrize(
    "value", [None, 0, -1, nan, inf, "12", "not-a-duration"])
def test_unknown_duration_values_follow_the_explicit_visibility_toggle(value):
    unknown = clip("unknown", value)

    assert ClipFilter(minimum=10).matches(unknown)
    assert not ClipFilter(minimum=10, show_unknown=False).matches(unknown)
    assert ClipFilter(minimum=10, show_unknown=True).matches(unknown)


def test_review_predicate_composes_with_duration_without_knowing_review_constants():
    clips = [
        clip("short-keep", 9, "keep"),
        clip("long-keep", 20, "keep"),
        clip("long-maybe", 20, "maybe"),
        clip("long-reject", 20, "reject"),
    ]
    reviewed = lambda one: one.review in {"keep", "maybe"}

    policy = ClipFilter(minimum=10, review_filter=reviewed)

    assert names(policy.apply(clips)) == ["long-keep", "long-maybe"]


def test_applying_a_filter_preserves_input_order_identity_and_values():
    clips = [clip("first", 5, "keep"), clip("second", 15, "maybe")]
    before = [(one.name, one.duration, one.review) for one in clips]

    selected = ClipFilter(minimum=10).apply(clips)

    assert selected == [clips[1]]
    assert [(one.name, one.duration, one.review) for one in clips] == before
    assert selected[0] is clips[1]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"minimum": -1}, ValueError),
        ({"maximum": -1}, ValueError),
        ({"minimum": nan}, ValueError),
        ({"maximum": inf}, ValueError),
        ({"minimum": "10"}, TypeError),
        ({"maximum": object()}, TypeError),
        ({"minimum": True}, TypeError),
        ({"minimum": 20, "maximum": 10}, ValueError),
        ({"show_unknown": 1}, TypeError),
        ({"review_filter": "keep"}, TypeError),
    ],
)
def test_malformed_bounds_and_policy_values_are_rejected_explicitly(kwargs, error):
    with pytest.raises(error):
        ClipFilter(**kwargs)

import pytest

from hearthfetch.observability import Metrics, UnsafeLabel, audit

# The things this service handles that must never become a label or a field.
SENSITIVE = [
    "https://evil.example/collect?d=SECRET",
    "when is the next transit",
    "The article reports that the bridge reopened.",
    "user-42@example.org",
    "sk-abc123DEF456",
    "hf1.AAAA.BBBB",
    "10.0.0.5",
]


@pytest.mark.parametrize("value", SENSITIVE)
def test_no_sensitive_value_can_become_a_metric_label(value):
    """Enforced by shape, not by remembering. Free text cannot match the pattern."""
    with pytest.raises(UnsafeLabel):
        Metrics().increment("hearthfetch_tool_calls", tool=value)


@pytest.mark.parametrize("value", SENSITIVE)
def test_no_sensitive_value_can_become_an_audit_field(value):
    with pytest.raises(UnsafeLabel):
        audit(event="source_rejected", rule=value)


def test_the_rejection_message_does_not_echo_the_value_it_rejected():
    """An exception message is a log line, which is the thing being protected."""
    secret = "https://evil.example/collect?d=SECRET"
    with pytest.raises(UnsafeLabel) as caught:
        Metrics().increment("x", tool=secret)
    assert secret not in str(caught.value)
    assert "evil.example" not in str(caught.value)


def test_audit_refuses_a_field_outside_the_closed_set():
    with pytest.raises(UnsafeLabel):
        audit(event="ok", url="https://a.example")
    assert audit(event="source_rejected", stage="input_scrub") == {
        "event": "source_rejected",
        "stage": "input_scrub",
    }


def test_safe_tokens_are_accepted():
    metrics = Metrics()
    metrics.increment("hearthfetch_tool_calls", tool="search_web", outcome="ok")
    metrics.increment("hearthfetch_tool_calls", tool="search_web", outcome="ok")
    metrics.increment("hearthfetch_source_rejections", stage="classify")
    assert metrics.value("hearthfetch_tool_calls", tool="search_web", outcome="ok") == 2
    assert metrics.value("hearthfetch_source_rejections", stage="classify") == 1


def test_render_is_prometheus_shaped_and_label_ordering_is_stable():
    metrics = Metrics()
    metrics.increment("calls", outcome="ok", tool="search_web")
    metrics.observe("dur_seconds", 0.5)
    metrics.observe("dur_seconds", 1.5)
    rendered = metrics.render()
    assert 'calls{outcome="ok",tool="search_web"} 1' in rendered
    assert "dur_seconds_count 2" in rendered
    assert "dur_seconds_sum 2.000000" in rendered


def test_a_label_name_must_also_be_a_safe_identifier():
    with pytest.raises(UnsafeLabel):
        Metrics().increment("x", **{"Weird-Name": "ok"})


def test_cardinality_follows_from_the_shape_rule():
    """A label that cannot hold a URL cannot hold ten thousand values either."""
    metrics = Metrics()
    for i in range(50):
        with pytest.raises(UnsafeLabel):
            metrics.increment("x", tool=f"https://host{i}.example/{i}")
    assert metrics.counters == {}

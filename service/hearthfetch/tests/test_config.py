import pytest

from hearthfetch.config import Config


def test_defaults_are_safe(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("HEARTHFETCH_"):
            monkeypatch.delenv(key, raising=False)
    config = Config.from_env()
    # The two decisions that matter, defaulted to the safer side.
    assert config.fetch_url_enabled is False
    assert config.scrub_policy.css_hidden_rejects is True


def test_the_model_choice_is_configuration_not_code(monkeypatch):
    monkeypatch.setenv("HEARTHFETCH_DISTILLER_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("HEARTHFETCH_CLASSIFIER_MODEL", "gpt-5.4-mini")
    config = Config.from_env()
    assert config.distiller.model.name == "gpt-5.6-luna"
    assert config.classifier.model.name == "gpt-5.4-mini"


def test_distiller_and_classifier_are_configured_independently(monkeypatch):
    monkeypatch.setenv("HEARTHFETCH_DISTILLER_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("HEARTHFETCH_CLASSIFIER_TIMEOUT_SECONDS", "5")
    config = Config.from_env()
    assert config.distiller.model.timeout_seconds == 45.0
    assert config.classifier.model.timeout_seconds == 5.0


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False),
])
def test_boolean_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("HEARTHFETCH_FETCH_URL_ENABLED", raw)
    assert Config.from_env().fetch_url_enabled is expected


@pytest.mark.parametrize("var", [
    "HEARTHFETCH_FETCH_URL_ENABLED",
    "HEARTHFETCH_CSS_HIDDEN_REJECTS",
])
def test_an_unparseable_boolean_fails_loudly_rather_than_defaulting(monkeypatch, var):
    """A typo must not silently disable a control."""
    monkeypatch.setenv(var, "maybe")
    with pytest.raises(ValueError):
        Config.from_env()


def test_an_unparseable_number_fails_loudly(monkeypatch):
    monkeypatch.setenv("HEARTHFETCH_FETCH_MAX_BYTES", "lots")
    with pytest.raises(ValueError):
        Config.from_env()

"""Product identity and local-build version tests."""

from unittest.mock import patch


async def test_version_endpoint_reports_campaign_intelligence_identity():
    from analytics_agent.api import get_version

    with patch("importlib.metadata.version", return_value="0.5.0"):
        result = await get_version()

    assert result == {
        "product": "Campaign Intelligence",
        "current_version": "0.5.0",
        "distribution": "local-build",
    }


async def test_version_endpoint_source_checkout_falls_back_to_pyproject():
    from analytics_agent.api import get_version

    with patch("importlib.metadata.version", side_effect=Exception("not installed")):
        result = await get_version()

    assert result["current_version"] == "0.5.0"


def test_app_version_uses_package_metadata(monkeypatch):
    from analytics_agent.main import create_app

    monkeypatch.delenv("ANALYTICS_AGENT_OVERRIDE_VERSION", raising=False)
    with patch("importlib.metadata.version", return_value="9.9.9"):
        app = create_app()

    assert app.title == "Campaign Intelligence API"
    assert app.version == "9.9.9"


def test_app_version_respects_override_env(monkeypatch):
    from analytics_agent.main import create_app

    monkeypatch.setenv("ANALYTICS_AGENT_OVERRIDE_VERSION", "1.2.3.dev0")
    app = create_app()

    assert app.version == "1.2.3.dev0"

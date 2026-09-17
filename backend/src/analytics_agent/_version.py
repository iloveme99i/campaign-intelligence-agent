"""Single source of truth for the running package version.

Both the FastAPI app constructor (``main.create_app``) and the
``GET /api/version`` endpoint read the version through :func:`get_package_version`
so there is no hardcoded version string to drift out of sync with the package
metadata.
"""

from __future__ import annotations

import importlib.metadata
import os
import tomllib
from pathlib import Path

PACKAGE_NAME = "campaign-intelligence"


def get_package_version() -> str:
    """Return the installed package version.

    Resolution order:

    1. ``ANALYTICS_AGENT_OVERRIDE_VERSION`` env var (used by dev/CI builds).
    2. ``importlib.metadata.version`` for the installed distribution.
    3. Local ``pyproject.toml`` when running from a source checkout.
    4. ``"unknown"`` when neither package metadata nor project metadata exists.
    """
    try:
        return os.environ.get("ANALYTICS_AGENT_OVERRIDE_VERSION") or importlib.metadata.version(
            PACKAGE_NAME
        )
    except Exception:
        try:
            project_file = Path(__file__).parents[3] / "pyproject.toml"
            with project_file.open("rb") as handle:
                return str(tomllib.load(handle)["project"]["version"])
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
            return "unknown"

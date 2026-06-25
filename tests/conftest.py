"""Pytest fixtures for the Dashie integration tests."""
import pathlib

import pytest
import pytest_homeassistant_custom_component

# aiodns/pycares lazily spawns a shared daemon thread on first DNS use, which
# pytest-homeassistant's strict leak-check flags against whichever test resolves
# first. Pre-spawn it here (at import, before the per-test thread baseline) so it
# belongs to the baseline for every test.
try:  # pragma: no cover - environment dependent
    import pycares

    pycares.Channel()
except Exception:  # pragma: no cover
    pass

pytest_plugins = "pytest_homeassistant_custom_component"

# HA's test `hass` fixture uses pytest-homeassistant-custom-component's bundled
# `testing_config` as the config dir, and discovers custom integrations from
# `<config_dir>/custom_components`. Symlink our integration in there so the real
# loader/flow machinery can find `dashie`. (venv-local; not committed.)
_TESTING_CC = (
    pathlib.Path(pytest_homeassistant_custom_component.__file__).parent
    / "testing_config"
    / "custom_components"
)
_REPO_DASHIE = pathlib.Path(__file__).parent.parent / "custom_components" / "dashie"
_LINK = _TESTING_CC / "dashie"
if not _LINK.exists():
    _TESTING_CC.mkdir(parents=True, exist_ok=True)
    _LINK.symlink_to(_REPO_DASHIE, target_is_directory=True)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make Home Assistant load the custom_components/dashie integration in tests."""
    yield


@pytest.fixture(autouse=True)
def _skip_stream_dependency(hass):
    """The manifest depends on `stream` (needs PyAV); it isn't exercised by the
    config flow, so mark it already set up to skip its dependency setup."""
    hass.config.components.add("stream")
    yield

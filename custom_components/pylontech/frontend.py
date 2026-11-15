"""Serve and auto-load the bundled Pylontech battery card.

The card in ./frontend is adapted from jtubb's Pylontech Battery Card (MIT,
see frontend/README.md). Registering it here means it shows up in the card
picker, and the generated dashboard works, without adding a resource by hand.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, LOGGER

CARD_TYPE = "custom:pylontech-battery-card"
CARD_FILE = "pylontech-battery-card.js"
# keep in step with CARD_VERSION in the .js: it busts the browser cache
CARD_VERSION = "1.0.0"
STATIC_URL = f"/{DOMAIN}_frontend"
_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve ./frontend and load the card on every page. Runs once per HA start."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False
            )
        ]
    )
    add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={CARD_VERSION}")
    LOGGER.debug("Bundled battery card v%s registered", CARD_VERSION)

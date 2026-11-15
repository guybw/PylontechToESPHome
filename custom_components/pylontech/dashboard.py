"""Build and create the optional "Pylontech" dashboard.

One view: the bundled battery card (see frontend.py), then the energy and
balance figures and two history graphs from built-in cards.

Entities are found by unique_id, never by entity_id, so renamed entities keep
working. Anything missing or disabled is simply left out.

Home Assistant has no public API for an integration to create a dashboard,
so this uses the lovelace component's internals. If that ever fails, setup
carries on and the user gets a notification plus a YAML copy to paste in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, LOGGER
from .frontend import CARD_TYPE

PENDING_KEY = f"{DOMAIN}_pending_dashboard"
DASHBOARD_ICON = "mdi:home-battery"
DASHBOARD_TITLE = "Pylontech"


def request_dashboard(hass: HomeAssistant, host: str) -> None:
    """Called by the config flow: build the dashboard once the entry is set up."""
    hass.data.setdefault(PENDING_KEY, set()).add(host)


def take_dashboard_request(hass: HomeAssistant, host: str) -> bool:
    pending = hass.data.get(PENDING_KEY, set())
    if host in pending:
        pending.discard(host)
        return True
    return False


def dashboard_url(entry: ConfigEntry) -> str:
    """Dashboard URL path. Home Assistant requires a hyphen in it."""
    return f"pylontech-{entry.entry_id[-6:].lower()}"


class _Resolver:
    """Look the stack's entities up by the key at the end of their unique_id."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._registry = er.async_get(hass)
        self._entry_id = entry.entry_id

    def __call__(self, domain: str, key: str) -> str | None:
        entity_id = self._registry.async_get_entity_id(
            domain, DOMAIN, f"{self._entry_id}_{key}"
        )
        if entity_id is None:
            return None
        entity = self._registry.async_get(entity_id)
        if entity is None or entity.disabled_by is not None:
            return None
        return entity_id

    def rows(self, items: list[tuple[str, str]]) -> list[str]:
        return [e for domain, key in items if (e := self(domain, key))]


def _entities_card(title: str, rows: list[str]) -> dict | None:
    return {"type": "entities", "title": title, "entities": rows} if rows else None


def _history_card(title: str, rows: list[str]) -> dict | None:
    if not rows:
        return None
    return {"type": "history-graph", "title": title, "hours_to_show": 24, "entities": rows}


def build_dashboard_config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return the dashboard config for one stack."""
    e = _Resolver(hass, entry)
    stack = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )

    battery_card: dict[str, Any] = {"type": CARD_TYPE}
    if stack is not None:
        battery_card["device_id"] = stack.id

    cards = [
        battery_card,
        _entities_card(
            "Energy",
            e.rows(
                [
                    ("sensor", "energy_charged"),
                    ("sensor", "energy_discharged"),
                    ("sensor", "stored_energy"),
                    ("sensor", "time_to_full"),
                    ("sensor", "time_to_empty"),
                ]
            ),
        ),
        _entities_card(
            "Balance and health",
            e.rows(
                [
                    ("sensor", "balance_status"),
                    ("sensor", "balance_score"),
                    ("sensor", "last_full_charge"),
                    ("sensor", "force_balance"),
                    ("sensor", "health"),
                    ("sensor", "cell_voltage_delta"),
                    ("sensor", "soh"),
                    ("sensor", "cycle_count"),
                    ("binary_sensor", "problem"),
                ]
            ),
        ),
        _history_card("State of charge (24 h)", e.rows([("sensor", "soc")])),
        _history_card("Power (24 h)", e.rows([("sensor", "power")])),
        _history_card(
            "Cell voltage delta (24 h)", e.rows([("sensor", "cell_voltage_delta")])
        ),
    ]
    return {
        "views": [
            {
                "title": "Battery",
                "path": "battery",
                "icon": DASHBOARD_ICON,
                # one centred column: the card, then the detail under it
                "type": "sections",
                "max_columns": 1,
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            {**card, "grid_options": {"columns": "full"}}
                            for card in cards
                            if card
                        ],
                    }
                ],
            }
        ]
    }


def _find_dashboards_collection(hass: HomeAssistant):
    """Return lovelace's DashboardsCollection, reached via its websocket handler.

    The collection isn't stored anywhere public; the websocket command
    handlers are bound methods of an object that holds it.
    """
    handlers = hass.data.get("websocket_api", {})
    for command in ("lovelace/dashboards/create", "lovelace/dashboards/list"):
        handler = handlers.get(command, (None,))[0]
        while handler is not None:
            collection = getattr(
                getattr(handler, "__self__", None), "storage_collection", None
            )
            if collection is not None:
                return collection
            handler = getattr(handler, "__wrapped__", None)
    return None


async def _async_save_yaml_copy(
    hass: HomeAssistant, url_path: str, config: dict
) -> str:
    from homeassistant.util.yaml import dump

    path = Path(hass.config.path(f"{url_path.replace('-', '_')}_dashboard.yaml"))
    text = dump(config)
    await hass.async_add_executor_job(path.write_text, text, "utf-8")
    return str(path)


async def async_create_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create (or rebuild) the Pylontech dashboard. Never raises."""
    config = build_dashboard_config(hass, entry)
    url_path = dashboard_url(entry)
    notification_id = f"{DOMAIN}_dashboard_{entry.entry_id}"
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        dashboards = hass.data[LOVELACE_DATA].dashboards
        if url_path not in dashboards:
            collection = _find_dashboards_collection(hass)
            if collection is None:
                raise RuntimeError("dashboards collection not found")
            await collection.async_create_item(
                {
                    "url_path": url_path,
                    "title": DASHBOARD_TITLE,
                    "icon": DASHBOARD_ICON,
                    "show_in_sidebar": True,
                    "require_admin": False,
                }
            )
        await dashboards[url_path].async_save(config)
        LOGGER.info("Pylontech dashboard created at /%s", url_path)
        persistent_notification.async_create(
            hass,
            f"Your Pylontech dashboard is ready: [{DASHBOARD_TITLE}](/{url_path}/battery). "
            "You can edit it like any other dashboard.",
            title="Pylontech dashboard",
            notification_id=notification_id,
        )
    except Exception as err:  # noqa: BLE001 - internals changed: degrade gracefully
        LOGGER.warning("Could not create the Pylontech dashboard automatically: %s", err)
        try:
            path = await _async_save_yaml_copy(hass, url_path, config)
            where = f"A copy has been saved to `{path}`."
        except Exception as save_err:  # noqa: BLE001
            LOGGER.warning("Could not save the dashboard YAML: %s", save_err)
            where = "The dashboard YAML could not be saved either - please report this."
        persistent_notification.async_create(
            hass,
            "The Pylontech dashboard couldn't be created automatically on this "
            f"Home Assistant version. {where}\n\n"
            "To add it by hand: Settings → Dashboards → Add dashboard → New dashboard "
            "from scratch, open it, ⋮ → Edit → ⋮ → Raw configuration editor, "
            "and paste the file's contents. Or just add the **Pylontech Battery** "
            "card to any dashboard from the card picker.",
            title="Pylontech dashboard",
            notification_id=notification_id,
        )

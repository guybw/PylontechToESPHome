"""Constants for the Pylontech (ESPHome serial bridge) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

DOMAIN = "pylontech"
LOGGER = logging.getLogger(__package__)

# config entry keys
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_PROXY_NAME = "proxy_name"
CONF_CELL_SENSORS = "cell_sensors"
CONF_SYNC_TIME = "sync_time"

DEFAULT_PORT = 6053
DEFAULT_PROXY_NAME = "Pylontech Console"
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 15

# how often to also pull `stat` (cycle count etc.) — multiples of the poll
STAT_REFRESH_INTERVAL = timedelta(minutes=30)

# bridge timings
CONNECT_TIMEOUT = 30.0
COMMAND_TIMEOUT = 8.0
LOGIN_COMMAND = "login debug"

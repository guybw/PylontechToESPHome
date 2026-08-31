"""Pure parsers for the Pylontech console protocol.

No Home Assistant imports here on purpose: this module is unit-tested on its
own against real captured command output (see tests/fixtures/).

All electrical values are scaled to SI units for Home Assistant:
    mV -> V, mA -> A, mAH -> Ah, mC -> degC.
Cell-voltage spread ("delta") is kept in mV because that is how installers
think about balance.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# --- console framing --------------------------------------------------------

_PROMPTS = ("pylon_debug>", "pylon>")
_OK = "Command completed successfully"
_ERR_MARKERS = ("Invalid command", "Unknown command", "fail to excute")


def command_ok(raw: str) -> bool:
    """True if the console reported the command completed successfully."""
    return _OK in raw


def format_set_time(when: datetime) -> str:
    """Build the console command that sets the RTC.

    The console's own help says ``time [year] [month] [day] ...`` but only a
    2-digit year is accepted (this is the form the OEM BatteryView tool sends).
    """
    return f"time {when:%y %m %d %H %M %S}"


def command_error(raw: str) -> bool:
    """True if the console rejected the command."""
    return any(m in raw for m in _ERR_MARKERS)


def response_complete(raw: str) -> bool:
    """True once a full response has been received (end marker seen)."""
    return "\r\n$$" in raw or "\n$$" in raw or raw.rstrip().endswith(_PROMPTS)


def _body(raw: str) -> list[str]:
    """Return the payload lines: everything between the ``@`` echo marker and
    the ``$$`` / prompt trailer. Falls back to all lines if markers absent."""
    lines = raw.replace("\r\n", "\n").split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "@":
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s == "$$" or s in _PROMPTS or s == _OK:
            end = i
            break
    return lines[start:end]


# --- scaling helpers ------------------------------------------------------

def _milli(value: int | None, ndigits: int) -> float | None:
    return None if value is None else round(value / 1000, ndigits)


def _volt(mv: int | None) -> float | None:
    return _milli(mv, 3)


def _amp(ma: int | None) -> float | None:
    return _milli(ma, 3)


def _ah(mah: int | None) -> float | None:
    return _milli(mah, 3)


def _temp(mc: int | None) -> float | None:
    return _milli(mc, 1)


# --- pwrsys (system-level) ------------------------------------------------

_KV_NUM = re.compile(
    r"^\s*(?P<k>[A-Za-z][\w .@/]*?)\s*:\s*(?P<v>-?\d+)\s*(?P<u>mV|mA|mAH|mC|%)?\s*$"
)


def _num_fields(lines: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in lines:
        m = _KV_NUM.match(line)
        if m:
            out[m["k"].strip()] = int(m["v"])
    return out


def parse_pwrsys(raw: str) -> dict[str, Any]:
    """Parse the ``pwrsys`` response into scaled system metrics."""
    f = _num_fields(_body(raw))

    def iv(key: str) -> int | None:
        return f.get(key)

    voltage = _volt(iv("System Volt"))
    current = _amp(iv("System Curr"))
    cv_min = iv("Lowest voltage")
    cv_max = iv("Highest voltage")

    data: dict[str, Any] = {
        "voltage": voltage,
        "current": current,
        "power": None if (voltage is None or current is None) else round(voltage * current, 1),
        "soc": iv("System SOC"),
        "soh": iv("System SOH"),
        "remaining_capacity": _ah(iv("System RC")),
        "full_charge_capacity": _ah(iv("System FCC")),
        "cell_voltage_min": _volt(cv_min),
        "cell_voltage_max": _volt(cv_max),
        "cell_voltage_avg": _volt(iv("Average voltage")),
        "cell_voltage_delta": None if (cv_min is None or cv_max is None) else cv_max - cv_min,
        "temperature_min": _temp(iv("Lowest temperature")),
        "temperature_max": _temp(iv("Highest temperature")),
        "temperature_avg": _temp(iv("Average temperature")),
        "modules_total": iv("Total Num"),
        "modules_present": iv("Present Num"),
        "modules_sleep": iv("Sleep Num"),
        "recommend_charge_voltage": _volt(iv("Recommend chg voltage")),
        "recommend_discharge_voltage": _volt(iv("Recommend dsg voltage")),
        "recommend_charge_current": _amp(iv("Recommend chg current")),
        "recommend_discharge_current": _amp(iv("Recommend dsg current")),
    }
    data["charging"] = None if current is None else current > 0.05
    return data


# --- pwr (per-module summary table) -------------------------------------

_PWR_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<volt>-?\d+)\s+(?P<curr>-?\d+)\s+(?P<tempr>-?\d+)\s+"
    r"(?P<tlow>-?\d+)\s+(?P<thigh>-?\d+)\s+(?P<vlow>-?\d+)\s+(?P<vhigh>-?\d+)\s+"
    r"(?P<base>\S+)\s+(?P<vst>\S+)\s+(?P<cst>\S+)\s+(?P<tst>\S+)\s+(?P<soc>\d+)%\s+"
    r"(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(?P<bvst>\S+)\s+(?P<btst>\S+)"
    r"(?:\s+(?P<mos>-?\d+|-)\s+(?P<mtst>\S+))?\s*$"
)
_PWR_ABSENT = re.compile(r"^\s*(?P<idx>\d+)\s+-\s+.*\bAbsent\b", re.IGNORECASE)

_NORMAL = "Normal"


def parse_pwr(raw: str) -> dict[int, dict[str, Any]]:
    """Parse ``pwr`` into ``{module_number: {metrics...}}`` for present modules."""
    modules: dict[int, dict[str, Any]] = {}
    for line in _body(raw):
        if _PWR_ABSENT.match(line):
            continue
        m = _PWR_ROW.match(line)
        if not m:
            continue
        vlow = int(m["vlow"])
        vhigh = int(m["vhigh"])
        voltage = _volt(int(m["volt"]))
        current = _amp(int(m["curr"]))
        mos = m["mos"]
        states = [m["vst"], m["cst"], m["tst"], m["bvst"], m["btst"]]
        modules[int(m["idx"])] = {
            "present": True,
            "voltage": voltage,
            "current": current,
            "power": None if (voltage is None or current is None) else round(voltage * current, 1),
            "temperature": _temp(int(m["tempr"])),
            "cell_temp_min": _temp(int(m["tlow"])),
            "cell_temp_max": _temp(int(m["thigh"])),
            "cell_voltage_min": _volt(vlow),
            "cell_voltage_max": _volt(vhigh),
            "cell_voltage_delta": vhigh - vlow,
            "soc": int(m["soc"]),
            "base_state": m["base"],
            "volt_state": m["vst"],
            "curr_state": m["cst"],
            "temp_state": m["tst"],
            "bv_state": m["bvst"],
            "bt_state": m["btst"],
            "mos_temperature": None if mos in (None, "-") else _temp(int(mos)),
            "mt_state": None if m["mtst"] in (None, "-") else m["mtst"],
            "timestamp": m["time"],
            "problem": any(s != _NORMAL for s in states),
        }
    return modules


# --- info (device identity) --------------------------------------------

_KV_STR = re.compile(r"^\s*(?P<k>[A-Za-z][\w .@/]*?)\s*:\s*(?P<v>.+?)\s*$")

_INFO_MAP = {
    "Manufacturer": "manufacturer",
    "Device name": "model",
    "Board version": "board_version",
    "Main Soft version": "main_soft_version",
    "Soft version": "soft_version",
    "Boot version": "boot_version",
    "Comm version": "comm_version",
    "Release Date": "release_date",
    "Barcode": "barcode",
    "Specification": "specification",
}


def parse_info(raw: str) -> dict[str, Any]:
    """Parse ``info`` into normalised device-identity fields."""
    raw_kv: dict[str, str] = {}
    for line in _body(raw):
        m = _KV_STR.match(line)
        if m:
            raw_kv[re.sub(r"\s+", " ", m["k"]).strip()] = m["v"].strip()

    out: dict[str, Any] = {dest: raw_kv[src] for src, dest in _INFO_MAP.items() if src in raw_kv}

    if "Cell Number" in raw_kv and raw_kv["Cell Number"].isdigit():
        out["cell_count"] = int(raw_kv["Cell Number"])
    for src, dest in (("Max Charge Curr", "max_charge_current"),
                      ("Max Dischg Curr", "max_discharge_current")):
        if src in raw_kv:
            mm = re.search(r"-?\d+", raw_kv[src])
            if mm:
                out[dest] = _amp(int(mm.group()))
    return out


# --- stat (lifetime counters) ----------------------------------------

# Only the counters that are stable lifetime values. "Charge Cnt.",
# "Discharge Cnt.", "Status Cnt.", "Idle Times" are rolling/resettable on the
# battery, so they are left in ``raw`` only.
_STAT_MAP = {
    "CYCLE Times": "cycle_count",
    "SOH": "soh",
    "Pwr Percent": "pwr_percent",
    "Charge Times": "charge_times",
    "Shut Times": "shutdown_count",
    "Reset Times": "reset_count",
}


def parse_stat(raw: str) -> dict[str, Any]:
    """Parse ``stat`` counters. Returns curated keys plus ``raw`` (all ints)."""
    f = _num_fields(_body(raw))
    out: dict[str, Any] = {dest: f[src] for src, dest in _STAT_MAP.items() if src in f}
    out["raw"] = f
    return out


# --- bat N (per-cell voltages for one module) -----------------------

_BAT_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<volt>\d+)\s+(?P<curr>-?\d+)\s+(?P<tempr>-?\d+)\s+"
    r"\S+\s+\S+\s+\S+\s+\S+\s+(?P<soc>\d+)%\s+(?P<coulomb>\d+)\s*mAH\s+(?P<bal>[YN])\s*$"
)


# --- cell-balance health verdict ----------------------------------------
#
# Heuristic from simonpasley/pylontech-battery-health: judge a module by its
# cell-voltage spread, but only when the measurement conditions are valid
# (enough load or a clean idle plateau, sensible SoC, not cold). Thresholds in
# mV: (warn, fail).

_HEALTH_LOADED = (30, 50)
_HEALTH_IDLE = (20, 40)
_LOAD_CURRENT_A = 0.2

HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADING = "degrading"
HEALTH_FAILED = "failed"
HEALTH_UNKNOWN = "unknown"
HEALTH_STATES = [HEALTH_HEALTHY, HEALTH_DEGRADING, HEALTH_FAILED, HEALTH_UNKNOWN]

_HEALTH_RANK = {HEALTH_HEALTHY: 0, HEALTH_UNKNOWN: 1, HEALTH_DEGRADING: 2, HEALTH_FAILED: 3}


def assess_cell_health(
    delta_mv: int | None,
    current_a: float | None,
    soc: int | None,
    temp_c: float | None,
) -> tuple[str, str]:
    """Return ``(verdict, condition)`` for one module's cell balance."""
    if None in (delta_mv, current_a, soc, temp_c):
        return HEALTH_UNKNOWN, "no_data"
    if temp_c <= 5:
        return HEALTH_UNKNOWN, "too_cold"

    if abs(current_a) >= _LOAD_CURRENT_A:
        if not 15 <= soc <= 95:
            return HEALTH_UNKNOWN, "soc_out_of_window"
        warn, fail = _HEALTH_LOADED
        condition = "under_load"
    else:
        if not 10 <= soc <= 85:
            return HEALTH_UNKNOWN, "soc_out_of_window"
        warn, fail = _HEALTH_IDLE
        condition = "idle"

    if delta_mv > fail:
        return HEALTH_FAILED, condition
    if delta_mv > warn:
        return HEALTH_DEGRADING, condition
    return HEALTH_HEALTHY, condition


def worst_health(verdicts: list[str]) -> str:
    """Combine per-module verdicts into a stack-level verdict."""
    if not verdicts:
        return HEALTH_UNKNOWN
    return max(verdicts, key=lambda v: _HEALTH_RANK.get(v, 1))


def parse_bat(raw: str) -> list[dict[str, Any]]:
    """Parse ``bat N`` into a list of per-cell dicts (index, voltage, balancing)."""
    cells: list[dict[str, Any]] = []
    for line in _body(raw):
        m = _BAT_ROW.match(line)
        if not m:
            continue
        cells.append(
            {
                "index": int(m["idx"]),
                "voltage": _volt(int(m["volt"])),
                "balancing": m["bal"] == "Y",
            }
        )
    return cells


def summarise_cells(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Weakest-cell / spread / balancing summary for a module's cell list."""
    volts = [(c["index"], c["voltage"]) for c in cells if c.get("voltage") is not None]
    if not volts:
        return {}
    lo_i, lo_v = min(volts, key=lambda t: t[1])
    hi_i, hi_v = max(volts, key=lambda t: t[1])
    return {
        "cell_count": len(cells),
        "spread_mv": round((hi_v - lo_v) * 1000),
        "weakest_cell": lo_i,
        "weakest_cell_voltage": lo_v,
        "weakest_cell_delta_mv": round((hi_v - lo_v) * 1000),
        "strongest_cell": hi_i,
        "balancing_cells": [c["index"] for c in cells if c.get("balancing")],
    }


# --- data event / data history (the on-device log) -------------------

_KV_ANY = re.compile(r"^\s*(?P<k>[A-Za-z][\w .]*?)\s*:\s*(?P<v>.*?)\s*$")

_EVENT_CELL_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<volt>\d+)\s+(?P<curr>-?\d+)\s+(?P<tempr>-?\d+)\s+"
    r"(?P<base>\S+)\s+(?P<vst>\S+)\s+(?P<cst>\S+)\s+(?P<tst>\S+)\s+(?P<soc>\d+)%\s*$"
)

_EVENT_NUM = {
    "Voltage": ("voltage", _volt),
    "Current": ("current", _amp),
    "Temperature": ("temperature", _temp),
    "Max Voltage": ("max_voltage", _volt),
    "Total Coulomb": ("coulomb", _ah),
}


def _plausible_timestamp(text: str | None) -> bool:
    """True if a ``YY-MM-DD HH:MM:SS`` string looks like a real date."""
    m = re.match(r"\s*(\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})", text or "")
    if not m:
        return False
    yy, mm, dd, hh, mi, ss = (int(x) for x in m.groups())
    return 20 <= yy <= 90 and 1 <= mm <= 12 and 1 <= dd <= 31 and hh < 24 and mi < 60 and ss < 60


def _hex_nonzero(text: str) -> bool:
    try:
        return int(text, 16) != 0
    except ValueError:
        return False


def _split_event_flags(text: str) -> tuple[str, str]:
    parts = text.split(None, 1)
    return (parts[0] if parts else "0x0"), (parts[1].strip() if len(parts) > 1 else "")


def parse_data_record(raw: str) -> dict[str, Any]:
    """Parse one ``data event [N]`` / ``data history [N]`` record."""
    kv: dict[str, str] = {}
    cells: list[dict[str, Any]] = []
    for line in _body(raw):
        cell = _EVENT_CELL_ROW.match(line)
        if cell:
            cells.append(
                {
                    "index": int(cell["idx"]),
                    "voltage": _volt(int(cell["volt"])),
                    "temperature": _temp(int(cell["tempr"])),
                    "volt_state": cell["vst"],
                }
            )
            continue
        m = _KV_ANY.match(line)
        if m and m["v"] != "":
            kv[re.sub(r"\s+", " ", m["k"]).strip()] = m["v"].strip()

    idx = kv.get("Item Index") or kv.get("Rec Item Index")
    time_str = kv.get("Time")
    out: dict[str, Any] = {
        "index": int(idx) if idx and idx.lstrip("-").isdigit() else None,
        "time": time_str,
        # The battery RTC loses time on a full power-down; entries written before
        # it was set read as 00-00-00 / 24-00-00.
        "time_valid": _plausible_timestamp(time_str),
    }
    for src, (dst, conv) in _EVENT_NUM.items():
        if src in kv:
            mm = re.match(r"-?\d+", kv[src])
            out[dst] = conv(int(mm.group())) if mm else None
    if "Percent" in kv:
        mm = re.match(r"-?\d+", kv["Percent"])
        out["soc"] = int(mm.group()) if mm else None
    for src, dst in (
        ("Base State", "base_state"),
        ("Volt. State", "volt_state"),
        ("Curr. State", "curr_state"),
        ("Tempr. State", "temp_state"),
        ("Coul. Status", "coulomb_state"),
    ):
        if src in kv:
            out[dst] = kv[src]

    pe_raw, pe = _split_event_flags(kv.get("Power Events", "0x0"))
    be_raw, be = _split_event_flags(kv.get("Bat Events", "0x0"))
    out["power_events"] = pe
    out["power_events_raw"] = pe_raw
    out["bat_events"] = be
    out["bat_events_raw"] = be_raw
    out["system_fault"] = kv.get("System Fault", "0x0").strip()
    out["alarm"] = bool(
        pe or be or _hex_nonzero(out["system_fault"])
        or (out.get("base_state") not in (None, "Charge", "Dischg", "Idle"))
    )
    if cells:
        out["cells"] = cells
    return out

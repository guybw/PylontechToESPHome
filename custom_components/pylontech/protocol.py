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
from datetime import datetime, timedelta
from typing import Any

# --- console framing --------------------------------------------------------

_PROMPTS = ("pylon_debug>", "pylon>")
_OK = "Command completed successfully"
_ERR_MARKERS = ("Invalid command", "Unknown command", "fail to excute")


# Some Pylontech batteries stay silent on the 115200 console after a full
# power-off until they receive this frame at 1200 baud (the battery's EPON/link
# port rate). ASCII: "~20014682C0048520FCC3\r".
WAKE_FRAME = b"~20014682C0048520FCC3\r"


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
        # Whole-stack ratings (sum of all modules), as opposed to the
        # per-module "Recommend ..." lines above.
        "system_recommend_charge_voltage": _volt(iv("system Recommend chg voltage")),
        "system_recommend_discharge_voltage": _volt(iv("system Recommend dsg voltage")),
        "system_recommend_charge_current": _amp(iv("system Recommend chg current")),
        "system_recommend_discharge_current": _amp(iv("system Recommend dsg current")),
    }
    data["charging"] = None if current is None else current > 0.05
    return data


# --- pwr (per-module summary table) -------------------------------------

_PWR_ABSENT = re.compile(r"^\s*(?P<idx>\d+)\s+-\s+.*\bAbsent\b", re.IGNORECASE)

_NORMAL = "Normal"

_DATE_TOKEN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_TOKEN = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _split_pwr_row(line: str) -> list[str]:
    """Split a ``pwr`` line on whitespace, joining the ``Time`` date+time pair
    back into one token so it lines up with the header's single column."""
    tokens = line.split()
    merged: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and _DATE_TOKEN.match(tokens[i]) and _TIME_TOKEN.match(tokens[i + 1]):
            merged.append(f"{tokens[i]} {tokens[i + 1]}")
            i += 2
        else:
            merged.append(tokens[i])
            i += 1
    return merged


def _pwr_int(fields: dict[str, str], name: str) -> int | None:
    v = fields.get(name)
    return None if v in (None, "-") else int(v)


def _abnormal(*states: str | None) -> bool:
    return any(s not in (None, "-", _NORMAL) for s in states)


def _add_alarms(mod: dict[str, Any]) -> None:
    """Split the module's state columns into voltage / current / temperature alarms."""
    mod["voltage_alarm"] = _abnormal(mod.get("volt_state"), mod.get("bv_state"))
    mod["current_alarm"] = _abnormal(mod.get("curr_state"))
    mod["temperature_alarm"] = _abnormal(
        mod.get("temp_state"), mod.get("bt_state"), mod.get("mt_state")
    )


def parse_pwr(raw: str) -> dict[int, dict[str, Any]]:
    """Parse ``pwr`` into ``{module_number: {metrics...}}`` for present modules.

    Columns are matched against the response's own header instead of a fixed
    position: newer firmware has twice been seen inserting new columns (a
    ``*.Id`` column next to each min/max, then a trailing ``SysAlarm.St``),
    each time silently breaking a position-based parser. An unrecognised
    column is simply ignored rather than shifting every field after it.
    """
    modules: dict[int, dict[str, Any]] = {}
    columns: list[str] | None = None
    for line in _body(raw):
        if not line.strip():
            continue
        tokens = _split_pwr_row(line)
        if columns is None:
            if tokens[0] == "Power":
                columns = tokens[1:]
            continue
        if _PWR_ABSENT.match(line) or not tokens[0].isdigit():
            continue

        fields = dict(zip(columns, tokens[1:]))
        vlow, vhigh = _pwr_int(fields, "Vlow"), _pwr_int(fields, "Vhigh")
        voltage = _volt(_pwr_int(fields, "Volt"))
        current = _amp(_pwr_int(fields, "Curr"))
        mos = _pwr_int(fields, "MosTempr")
        soc = fields.get("Coulomb")
        mtst = fields.get("M.T.St")
        states = [fields.get(k) for k in ("Volt.St", "Curr.St", "Temp.St", "B.V.St", "B.T.St")]

        modules[int(tokens[0])] = {
            "present": True,
            "voltage": voltage,
            "current": current,
            "power": None if (voltage is None or current is None) else round(voltage * current, 1),
            "temperature": _temp(_pwr_int(fields, "Tempr")),
            "cell_temp_min": _temp(_pwr_int(fields, "Tlow")),
            "cell_temp_max": _temp(_pwr_int(fields, "Thigh")),
            "cell_voltage_min": _volt(vlow),
            "cell_voltage_max": _volt(vhigh),
            "cell_voltage_delta": None if (vlow is None or vhigh is None) else vhigh - vlow,
            "soc": None if soc in (None, "-") else int(soc.rstrip("%")),
            "base_state": fields.get("Base.St"),
            "volt_state": fields.get("Volt.St"),
            "curr_state": fields.get("Curr.St"),
            "temp_state": fields.get("Temp.St"),
            "bv_state": fields.get("B.V.St"),
            "bt_state": fields.get("B.T.St"),
            "mos_temperature": _temp(mos),
            "mt_state": None if mtst in (None, "-") else mtst,
            "timestamp": fields.get("Time"),
            "problem": any(s not in (None, _NORMAL) for s in states),
        }
        _add_alarms(modules[int(tokens[0])])
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

    # a high-voltage management unit leaves "Barcode" empty
    if "barcode" not in out and "Module Barcode" in raw_kv:
        out["barcode"] = raw_kv["Module Barcode"]
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

# The number of state columns between Tempr and SOC varies by model (US2000C:
# 4, US3000D: 5 with separate DTemp/CTemp), so match them as a variable run.
# The trailing BAL (Y/N) column is also model-dependent - some firmware
# (e.g. US2KBPL) omits it entirely - so it's optional too.
_BAT_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<volt>\d+)\s+(?P<curr>-?\d+)\s+(?P<tempr>-?\d+)\s+"
    r"(?:\S+\s+)+?(?P<soc>\d+)%\s+(?P<coulomb>\d+)\s*mAH(?:\s+(?P<bal>[YN]))?\s*$"
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
HEALTH_PENDING = "pending"
HEALTH_UNKNOWN = "unknown"
HEALTH_STATES = [
    HEALTH_HEALTHY,
    HEALTH_PENDING,
    HEALTH_DEGRADING,
    HEALTH_FAILED,
    HEALTH_UNKNOWN,
]

_HEALTH_RANK = {
    HEALTH_HEALTHY: 0,
    HEALTH_PENDING: 1,
    HEALTH_UNKNOWN: 2,
    HEALTH_DEGRADING: 3,
    HEALTH_FAILED: 4,
}


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
        return HEALTH_PENDING, "too_cold"

    if abs(current_a) >= _LOAD_CURRENT_A:
        if not 15 <= soc <= 95:
            return HEALTH_PENDING, "soc_out_of_window"
        warn, fail = _HEALTH_LOADED
        condition = "under_load"
    else:
        if not 10 <= soc <= 85:
            return HEALTH_PENDING, "soc_out_of_window"
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


# --- force balance session ------------------------------------------------
#
# There is no console command for this: the BMS only balances at the top of
# the charge, so a "force balance" means the inverter charges to 100 % and
# holds it there. We time the hold and compare each module's cell spread at
# the start of the hold with the average over its last 15 minutes.

BALANCE_TARGET_MV = 30
BALANCE_HOLD_SOC = 98
BALANCE_DROP_SOC = 95
BALANCE_HOLD = timedelta(hours=2)
BALANCE_WINDOW = timedelta(minutes=15)
BALANCE_TIMEOUT = timedelta(hours=48)

BALANCE_IDLE = "idle"
BALANCE_CHARGING = "charging"
BALANCE_HOLDING = "holding"
BALANCE_BALANCED = "balanced"
BALANCE_GOOD_PROGRESS = "good_progress"
BALANCE_CELL_BEHIND = "cell_behind"
BALANCE_TIMED_OUT = "timed_out"
BALANCE_STATES = [
    BALANCE_IDLE,
    BALANCE_CHARGING,
    BALANCE_HOLDING,
    BALANCE_BALANCED,
    BALANCE_GOOD_PROGRESS,
    BALANCE_CELL_BEHIND,
    BALANCE_TIMED_OUT,
]


def balance_step(
    session: dict[str, Any], now: datetime, soc: int | None, deltas: dict[int, int]
) -> dict[str, Any]:
    """Advance a force-balance session by one poll (mutates ``session``).

    ``deltas`` is ``{module: cell spread in mV}``.
    """
    state = session.get("state")
    if state == BALANCE_CHARGING:
        if now - session["started"] > BALANCE_TIMEOUT:
            session.update(state=BALANCE_TIMED_OUT, finished=now)
        elif soc is not None and soc >= BALANCE_HOLD_SOC and deltas:
            session.update(
                state=BALANCE_HOLDING,
                hold_started=now,
                hold_ends=now + BALANCE_HOLD,
                start_delta_mv=dict(deltas),
                samples=[],
            )
    elif state == BALANCE_HOLDING:
        if soc is not None and soc < BALANCE_DROP_SOC:
            # fell out of the full-charge window: the countdown restarts
            for key in ("hold_started", "hold_ends", "start_delta_mv", "samples"):
                session.pop(key, None)
            session["state"] = BALANCE_CHARGING
            return session
        if deltas:
            session["samples"].append((now, dict(deltas)))
        samples = [s for s in session["samples"] if now - s[0] <= BALANCE_WINDOW]
        session["samples"] = samples
        if now >= session["hold_ends"] and samples:
            start = session["start_delta_mv"]
            end: dict[int, int] = {}
            for num in start:
                vals = [d[num] for _, d in samples if num in d]
                if vals:
                    end[num] = round(sum(vals) / len(vals))
            over = {n: v for n, v in end.items() if v > BALANCE_TARGET_MV}
            # "good progress" = every module still over target narrowed by >= 1/4
            stuck = {n: v for n, v in over.items() if v > 0.75 * start[n]}
            if not over:
                state = BALANCE_BALANCED
            elif not stuck:
                state = BALANCE_GOOD_PROGRESS
            else:
                state = BALANCE_CELL_BEHIND
            del session["samples"]
            session.update(state=state, finished=now, end_delta_mv=end)
            worst = stuck or over
            if worst:
                session["module"] = max(worst, key=worst.get)
    return session


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


# --- energy counters / time remaining ---------------------------------------


def integrate_energy(
    totals: dict[str, float],
    prev_power: float | None,
    power: float | None,
    dt_s: float,
    max_gap_s: float,
) -> None:
    """Add one poll interval to ``totals`` (Wh, keys ``charged``/``discharged``).

    Trapezoid between the two power readings. A gap longer than ``max_gap_s``
    (restart, lost link) is skipped instead of guessed.
    """
    if prev_power is None or power is None or not 0 < dt_s <= max_gap_s:
        return
    wh = (prev_power + power) / 2 * dt_s / 3600
    if wh > 0:
        totals["charged"] = totals.get("charged", 0.0) + wh
    else:
        totals["discharged"] = totals.get("discharged", 0.0) - wh


_MIN_ETA_CURRENT_A = 0.5


def time_remaining(
    remaining_ah: float | None, full_ah: float | None, current_a: float | None
) -> tuple[float | None, float | None]:
    """Return ``(hours to full, hours to empty)`` at the present current."""
    if remaining_ah is None or current_a is None:
        return None, None
    if current_a >= _MIN_ETA_CURRENT_A and full_ah is not None:
        return round(max(full_ah - remaining_ah, 0.0) / current_a, 2), None
    if current_a <= -_MIN_ETA_CURRENT_A:
        return None, round(remaining_ah / -current_a, 2)
    return None, None


# --- automatic full-charge / balance tracking -------------------------------
#
# Every time the stack sits at full charge the BMS gets a chance to balance.
# We record each of those windows (cell spread per module at its start and
# averaged over its last 15 minutes) without anyone starting a session, and
# turn the latest one into a verdict and a score.
#
# The tracker is plain JSON (epoch seconds, string module keys) so it can be
# stored as-is.

TRACK_WEAK_MV = 100
TRACK_STALE = timedelta(days=14)
TRACK_KEEP = timedelta(days=30)

TRACK_BALANCED = "balanced"
TRACK_UNDER_WATCH = "under_watch"
TRACK_RECOMMENDED = "balance_recommended"
TRACK_WEAK_CELL = "weak_cell_suspected"
TRACK_STATES = [TRACK_BALANCED, TRACK_UNDER_WATCH, TRACK_RECOMMENDED, TRACK_WEAK_CELL]


def _window_end(window: dict[str, Any]) -> dict[str, int]:
    """Per-module spread averaged over the window's last 15 minutes at full."""
    samples = window["samples"]
    if not samples:
        return dict(window["start_delta_mv"])
    last = samples[-1][0]
    recent = [d for ts, d in samples if last - ts <= BALANCE_WINDOW.total_seconds()]
    out: dict[str, int] = {}
    for num in window["start_delta_mv"]:
        vals = [d[num] for d in recent if num in d]
        if vals:
            out[num] = round(sum(vals) / len(vals))
    return out


def _closed(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "started": window["started"],
        "ended": window.get("last", window["started"]),
        "start_delta_mv": window["start_delta_mv"],
        "end_delta_mv": _window_end(window),
    }


def track_full_charge(
    tracker: dict[str, Any], now: float, soc: int | None, deltas: dict[int, int]
) -> None:
    """Advance the full-charge tracker by one poll (mutates ``tracker``)."""
    history = tracker.setdefault("history", [])
    window = tracker.get("window")
    if soc is None:
        return
    spread = {str(num): mv for num, mv in deltas.items()}
    if window is None:
        if soc >= BALANCE_HOLD_SOC and spread:
            tracker["window"] = {
                "started": now,
                "start_delta_mv": spread,
                "samples": [[now, spread]],
            }
        return
    if soc < BALANCE_DROP_SOC:
        history.append(_closed(window))
        cutoff = now - TRACK_KEEP.total_seconds()
        tracker["history"] = [h for h in history if h["ended"] >= cutoff]
        tracker["window"] = None
    elif soc >= BALANCE_HOLD_SOC and spread:
        window["samples"].append([now, spread])
        cutoff = now - BALANCE_WINDOW.total_seconds()
        window["samples"] = [s for s in window["samples"] if s[0] >= cutoff]
        window["last"] = now


def balance_score(worst_mv: int) -> int:
    """100 at or under the 30 mV target, falling in a straight line to 0 at 200 mV."""
    if worst_mv <= BALANCE_TARGET_MV:
        return 100
    return max(0, round(100 - (worst_mv - BALANCE_TARGET_MV) * 100 / 170))


def balance_summary(tracker: dict[str, Any], now: float) -> dict[str, Any]:
    """Verdict, score and details of the most recent full charge."""
    history = tracker.get("history") or []
    window = tracker.get("window")
    latest = _closed(window) if window else (history[-1] if history else None)
    out: dict[str, Any] = {
        "state": TRACK_RECOMMENDED,
        "score": None,
        "at_full_charge": window is not None,
        "full_charges_30d": len(history) + (1 if window else 0),
    }
    if latest is None or not latest["end_delta_mv"]:
        return out
    end = latest["end_delta_mv"]
    worst_module = max(end, key=end.get)
    worst = end[worst_module]
    duration = latest["ended"] - latest["started"]
    out.update(
        score=balance_score(worst),
        last_full_charge=latest["ended"],
        duration_minutes=round(duration / 60),
        start_delta_mv=latest["start_delta_mv"],
        end_delta_mv=end,
        worst_module=int(worst_module),
        worst_delta_mv=worst,
    )
    if window is None and now - latest["ended"] > TRACK_STALE.total_seconds():
        out["state"] = TRACK_RECOMMENDED
    elif worst <= BALANCE_TARGET_MV:
        out["state"] = TRACK_BALANCED
    elif worst > TRACK_WEAK_MV and duration >= BALANCE_HOLD.total_seconds():
        out["state"] = TRACK_WEAK_CELL
    else:
        out["state"] = TRACK_UNDER_WATCH
    return out


# --- force-balance session <-> JSON (so a session survives a restart) -------

_SESSION_TIMES = ("started", "hold_started", "hold_ends", "finished")
_SESSION_DELTAS = ("start_delta_mv", "end_delta_mv")


def balance_dump(session: dict[str, Any]) -> dict[str, Any]:
    """Turn a force-balance session into plain JSON."""
    out = dict(session)
    for key in _SESSION_TIMES:
        if key in out:
            out[key] = out[key].isoformat()
    if "samples" in out:
        out["samples"] = [[ts.isoformat(), d] for ts, d in out["samples"]]
    return out


def balance_load(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Rebuild a force-balance session from :func:`balance_dump` output."""
    if not stored or stored.get("state") not in BALANCE_STATES:
        return {"state": BALANCE_IDLE}
    out = dict(stored)
    for key in _SESSION_TIMES:
        if key in out:
            out[key] = datetime.fromisoformat(out[key])
    for key in _SESSION_DELTAS:
        if key in out:
            out[key] = {int(k): v for k, v in out[key].items()}
    if "samples" in out:
        out["samples"] = [
            (datetime.fromisoformat(ts), {int(k): v for k, v in d.items()})
            for ts, d in out["samples"]
        ]
    return out


# --- high-voltage stacks (EXPERIMENTAL) ------------------------------------
#
# Force H / PowerCube towers: a management unit (SC0500/SC1000) in front of
# H48050-style modules. Same console, different tables: ``pwr`` is a single
# stack row, ``unit`` has one row per module and ``bat`` lists every cell of
# the stack. There is no ``pwrsys`` and no SOH / cycle count.
#
# Layouts taken from the captures in mletenay/home-assistant-pylontech (MIT),
# see tests/fixtures/hv/. Not yet run against a live stack.

_HV_COULOMB = re.compile(r"(\d+)%\s+(\d+)\s*(?:mAH|WH)")


def is_hv_pwr(raw: str) -> bool:
    """True if a ``pwr`` reply is the high-voltage single-row layout."""
    for line in _body(raw):
        tokens = line.split()
        if tokens and tokens[0] == "Power":
            return False
        if tokens and tokens[0] == "Volt" and "UVlow" in tokens:
            return True
    return False


def _hv_table(raw: str, lead: str) -> tuple[dict[str, int], list[dict[str, str]]]:
    """Return ``(key: value lines above the table, rows keyed by header name)``."""
    head: dict[str, int] = {}
    columns: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in _body(raw):
        if not line.strip():
            continue
        if columns is None:
            tokens = line.replace("Err Code", "Err.Code").split()
            if tokens[0] == lead:
                columns = tokens
            else:
                m = _KV_NUM.match(line)
                if m:
                    head[m["k"].strip()] = int(m["v"])
            continue
        # "15%   7621 mAH" is one header column: fold it into a single token
        tokens = _split_pwr_row(_HV_COULOMB.sub(r"\1%|\2", line))
        if tokens[0].lstrip("-").isdigit():
            rows.append(dict(zip(columns, tokens)))
    return head, rows


def _hv_coulomb(token: str | None) -> tuple[int | None, float | None]:
    """Split a folded ``15%|7621`` token into (percent, Ah)."""
    if not token or "|" not in token:
        return None, None
    pct, mah = token.split("|", 1)
    return int(pct.rstrip("%")), _ah(int(mah))


def _hv_common(f: dict[str, str]) -> dict[str, Any]:
    vlow, vhigh = _pwr_int(f, "BVlow"), _pwr_int(f, "BVhigh")
    voltage = _volt(_pwr_int(f, "Volt"))
    current = _amp(_pwr_int(f, "Curr"))
    soc, remaining = _hv_coulomb(f.get("CoulombAH"))
    return {
        "voltage": voltage,
        "current": current,
        "power": None if (voltage is None or current is None) else round(voltage * current, 1),
        "soc": soc,
        "remaining_capacity": remaining,
        "cell_voltage_min": _volt(vlow),
        "cell_voltage_max": _volt(vhigh),
        "cell_voltage_delta": None if (vlow is None or vhigh is None) else vhigh - vlow,
        "base_state": f.get("Base.St"),
        "volt_state": f.get("Volt.St"),
        "curr_state": f.get("Curr.St"),
        "temp_state": f.get("Temp.St"),
        "bv_state": f.get("B.V.St"),
        "bt_state": f.get("B.T.St"),
        "timestamp": f.get("Time"),
    }


def parse_pwr_hv(raw: str) -> dict[str, Any]:
    """Parse a high-voltage ``pwr`` reply into stack metrics (``pwrsys`` keys)."""
    head, rows = _hv_table(raw, "Volt")
    if not rows:
        return {}
    f = rows[0]
    data = _hv_common(f)
    error_code = f.get("Err.Code")
    data.update(
        temperature_min=_temp(_pwr_int(f, "BTlow")),
        temperature_max=_temp(_pwr_int(f, "BThigh")),
        temperature_avg=_temp(head.get("AverageTempr")),
        error_code=error_code,
        charging=None if data["current"] is None else data["current"] > 0.05,
        problem=_abnormal(
            data["volt_state"],
            data["curr_state"],
            data["temp_state"],
            data["bv_state"],
            data["bt_state"],
            f.get("U.V.St"),
            f.get("U.T.St"),
        )
        or (error_code is not None and _hex_nonzero(error_code)),
    )
    return data


def parse_unit_hv(raw: str) -> dict[int, dict[str, Any]]:
    """Parse a high-voltage ``unit`` reply into ``{module: metrics}`` (``pwr`` keys)."""
    _, rows = _hv_table(raw, "Index")
    modules: dict[int, dict[str, Any]] = {}
    for f in rows:
        mod = _hv_common(f)
        mod.update(
            present=True,
            temperature=_temp(_pwr_int(f, "Tempr")),
            cell_temp_min=_temp(_pwr_int(f, "BTlow")),
            cell_temp_max=_temp(_pwr_int(f, "BThigh")),
            mos_temperature=None,
            mt_state=None,
        )
        mod["problem"] = _abnormal(mod["volt_state"], mod["temp_state"])
        _add_alarms(mod)
        modules[int(f["Index"])] = mod
    return modules


_HV_BAT_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<volt>\d+)\s+(?P<curr>-?\d+)\s+(?P<tempr>-?\d+)\s+"
    r"\S+\s+\S+\s+.*?(?P<bal>[YN])\s*$"
)


def parse_bat_hv(raw: str, module_count: int) -> dict[int, list[dict[str, Any]]]:
    """Parse a high-voltage ``bat`` reply (every cell of the stack), split per module.

    Cells and modules are both numbered from the bottom of the tower, so
    module 1 owns the first block of cells.
    """
    cells = []
    for line in _body(raw):
        m = _HV_BAT_ROW.match(line)
        if m:
            cells.append({"voltage": _volt(int(m["volt"])), "balancing": m["bal"] == "Y"})
    if not module_count or not cells or len(cells) % module_count:
        return {}
    per = len(cells) // module_count
    return {
        num: [
            {"index": i, **cell}
            for i, cell in enumerate(cells[(num - 1) * per : num * per])
        ]
        for num in range(1, module_count + 1)
    }


_BMU_HEADER = re.compile(r"^\s*BMU\s+(\d+)\s+Barcode")
_BMU_MODULE = re.compile(r"^\s*Module:\s*:\s*(\S+)")


def parse_info_bmus(raw: str) -> dict[int, str]:
    """Module barcodes from a high-voltage ``info`` reply, keyed by BMU index.

    ``info`` counts BMUs from the top of the tower (BMU 0 is next to the
    management unit); ``unit`` and ``bat`` count from the bottom.
    """
    out: dict[int, str] = {}
    current: int | None = None
    for line in _body(raw):
        m = _BMU_HEADER.match(line)
        if m:
            current = int(m.group(1))
            continue
        m = _BMU_MODULE.match(line)
        if m and current is not None:
            out[current] = m.group(1)
            current = None
    return out

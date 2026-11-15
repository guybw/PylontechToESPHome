"""Parser tests against real captured Pylontech console output."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

# Load protocol.py directly (it has no Home Assistant imports) so these tests
# run without a HA install.
_PROTO = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "pylontech"
    / "protocol.py"
)
_spec = importlib.util.spec_from_file_location("pylontech_protocol", _PROTO)
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# --- framing --------------------------------------------------------------

def test_framing_flags():
    raw = load("pwrsys.txt")
    assert protocol.command_ok(raw)
    assert not protocol.command_error(raw)
    assert protocol.response_complete(raw)


def test_error_detection():
    assert protocol.command_error("Unknown command 'unit' - try 'help'\r\n$$\r\npylon>")
    assert not protocol.command_ok("Unknown command 'unit'")


# --- pwrsys -------------------------------------------------------------

def test_parse_pwrsys():
    d = protocol.parse_pwrsys(load("pwrsys.txt"))
    assert d["voltage"] == 50.012
    assert d["current"] == -5.76
    assert d["power"] == pytest.approx(-288.07, abs=0.05)
    assert d["soc"] == 99
    assert d["soh"] == 95
    assert d["remaining_capacity"] == 396.225
    assert d["full_charge_capacity"] == 400.104
    assert d["cell_voltage_min"] == 3.329
    assert d["cell_voltage_max"] == 3.337
    assert d["cell_voltage_avg"] == 3.334
    assert d["cell_voltage_delta"] == 8  # mV
    assert d["temperature_min"] == 22.7
    assert d["temperature_max"] == 26.0
    assert d["temperature_avg"] == 24.8
    assert d["modules_total"] == 8
    assert d["modules_present"] == 8
    assert d["modules_sleep"] == 0
    assert d["recommend_charge_voltage"] == 53.25
    assert d["recommend_discharge_current"] == -37.0
    assert d["system_recommend_charge_voltage"] == 53.25
    assert d["system_recommend_discharge_voltage"] == 45.0
    assert d["system_recommend_charge_current"] == 84.8
    assert d["system_recommend_discharge_current"] == -212.0
    assert d["charging"] is False


def test_parse_pwrsys_missing_fields_are_none():
    d = protocol.parse_pwrsys("cmd\r\n@\r\n garbage line\r\n$$\r\npylon_debug>")
    assert d["voltage"] is None
    assert d["power"] is None
    assert d["cell_voltage_delta"] is None
    assert d["charging"] is None


# --- pwr --------------------------------------------------------------

def test_parse_pwr_present_modules():
    mods = protocol.parse_pwr(load("pwr.txt"))
    assert sorted(mods) == [1, 2, 3, 4, 5, 6, 7, 8]  # 9..16 are Absent

    m1 = mods[1]
    assert m1["voltage"] == 49.98
    assert m1["current"] == -0.747
    assert m1["power"] == pytest.approx(-37.33, abs=0.05)
    assert m1["temperature"] == 26.0
    assert m1["cell_temp_min"] == 22.7
    assert m1["cell_temp_max"] == 23.4
    assert m1["cell_voltage_min"] == 3.33
    assert m1["cell_voltage_max"] == 3.333
    assert m1["cell_voltage_delta"] == 3  # mV
    assert m1["soc"] == 99
    assert m1["base_state"] == "Dischg"
    assert m1["mos_temperature"] == 24.4
    assert m1["mt_state"] == "Normal"
    assert m1["problem"] is False


def test_parse_pwr_module_without_mos_temp():
    mods = protocol.parse_pwr(load("pwr.txt"))
    assert mods[2]["mos_temperature"] is None
    assert mods[2]["mt_state"] is None
    assert mods[2]["voltage"] == 50.017


def test_parse_pwr_tolerates_unknown_columns():
    # Firmware B69.26.0.0 (issue #3) adds Tlow.Id/Thigh.Id/Vlow.Id/Vhigh.Id
    # columns next to each min/max, plus a trailing SysAlarm.St column.
    # Columns are matched by header name, so unknown ones must be ignored
    # rather than shifting every field that comes after them.
    raw = (
        "pwr\r\n@\r\n"
        "Power Volt   Curr   Tempr  Tlow   Tlow.Id  Thigh  Thigh.Id Vlow   Vlow.Id  "
        "Vhigh  Vhigh.Id Base.St  Volt.St  Curr.St  Temp.St  Coulomb  Time                 "
        "B.V.St   B.T.St  MosTempr M.T.St   SysAlarm.St\r\n"
        "1     49124  -1550  26000  23800  8        24600  0        3274   9        "
        "3275   0        Dischg   Normal   Normal   Normal   57%      2026-09-11 06:21:27  "
        "Normal   Normal  26200    Normal   Normal\r\n"
        "Command completed successfully\r\n$$\r\npylon_debug>"
    )
    m1 = protocol.parse_pwr(raw)[1]
    assert m1["voltage"] == 49.124
    assert m1["current"] == -1.55
    assert m1["cell_voltage_min"] == 3.274
    assert m1["cell_voltage_max"] == 3.275
    assert m1["soc"] == 57
    assert m1["base_state"] == "Dischg"
    assert m1["volt_state"] == "Normal"
    assert m1["mos_temperature"] == 26.2
    assert m1["problem"] is False


def test_parse_pwr_flags_problem_state():
    raw = load("pwr.txt").replace(
        "1     49980  -747   26000  22700  23400  3330   3333   Dischg   Normal   Normal   Normal",
        "1     49980  -747   26000  22700  23400  3330   3333   Dischg   OverVolt Normal   Normal",
    )
    assert protocol.parse_pwr(raw)[1]["problem"] is True


# --- info -----------------------------------------------------------

def test_parse_info():
    d = protocol.parse_info(load("info.txt"))
    assert d["manufacturer"] == "Pylon"
    assert d["model"] == "US3000C"
    assert d["board_version"] == "V10R04"
    assert d["main_soft_version"] == "B68.5.0.0"
    assert d["soft_version"] == "V2.8"
    assert d["barcode"] == "PPTCR00000000001"
    assert d["specification"] == "48V/74AH"
    assert d["cell_count"] == 15
    assert d["max_charge_current"] == 90.0
    assert d["max_discharge_current"] == -90.0


# --- stat -----------------------------------------------------------

def test_parse_stat():
    d = protocol.parse_stat(load("stat.txt"))
    assert d["cycle_count"] == 544
    assert d["soh"] == 95
    assert d["pwr_percent"] == 99
    assert d["charge_times"] == 2916
    assert d["shutdown_count"] == 194
    assert d["reset_count"] == 91
    # volatile counters stay in raw only
    assert d["raw"]["Discharge Cnt."] == 1118
    assert d["raw"]["DOC Times"] == 0
    assert d["raw"]["Bat HV Times"] == 333


# --- bat ----------------------------------------------------------

# --- cell health verdict ----------------------------------------

@pytest.mark.parametrize(
    ("delta", "current", "soc", "temp", "verdict"),
    [
        (8, -5.0, 95, 25, "healthy"),      # under load, tight spread
        (40, -5.0, 60, 25, "degrading"),   # under load, 30-50 mV
        (60, -5.0, 60, 25, "failed"),      # under load, >50 mV
        (8, 0.0, 60, 25, "healthy"),       # idle plateau, tight
        (30, 0.0, 60, 25, "degrading"),    # idle, 20-40 mV
        (50, 0.0, 60, 25, "failed"),       # idle, >40 mV
        (8, -5.0, 99, 25, "pending"),      # SoC above load window
        (8, 0.0, 5, 25, "pending"),        # SoC below idle window
        (8, -5.0, 60, 3, "pending"),       # too cold
        (None, -5.0, 60, 25, "unknown"),   # missing data
    ],
)
def test_assess_cell_health(delta, current, soc, temp, verdict):
    assert protocol.assess_cell_health(delta, current, soc, temp)[0] == verdict


def test_worst_health():
    assert protocol.worst_health(["healthy", "degrading", "healthy"]) == "degrading"
    assert protocol.worst_health(["healthy", "unknown", "failed"]) == "failed"
    assert protocol.worst_health(["healthy", "unknown"]) == "unknown"
    assert protocol.worst_health(["healthy", "pending"]) == "pending"
    assert protocol.worst_health(["pending", "unknown"]) == "unknown"
    assert protocol.worst_health([]) == "unknown"


def test_parse_bat():
    cells = protocol.parse_bat(load("bat1.txt"))
    assert len(cells) == 15
    assert cells[0] == {"index": 0, "voltage": 3.329, "balancing": False}
    assert cells[7]["voltage"] == 3.327
    assert all(c["balancing"] is False for c in cells)


def test_parse_bat_us3000d_extra_state_column():
    # US3000D splits Temp. State into DTemp. State + CTemp. State (issue #9)
    raw = (
        "bat 1\r\n@\r\n\r\n"
        "Battery  Volt     Curr     Tempr    Base State   Volt. State  Curr. State  DTemp. State CTemp. State SOC          Coulomb          BAL         \r\n"
        "0        3368     6005     26200    Charge       Normal       Normal       Normal       Normal       49%          36300        mAH N     \r\n"
        "14       3370     -230     25300    Dischg       Normal       Normal       Normal       Normal       98%          0            mAH Y     \r\n"
        "Command completed successfully\r\n$$\r\npylon>"
    )
    cells = protocol.parse_bat(raw)
    assert cells == [
        {"index": 0, "voltage": 3.368, "balancing": False},
        {"index": 14, "voltage": 3.370, "balancing": True},
    ]


def test_summarise_cells():
    s = protocol.summarise_cells(protocol.parse_bat(load("bat1.txt")))
    assert s["cell_count"] == 15
    assert s["weakest_cell"] == 7          # 3.327 V
    assert s["weakest_cell_voltage"] == 3.327
    assert s["spread_mv"] == 2             # 3.329 - 3.327
    assert s["balancing_cells"] == []


# --- data event / data history (on-device log) ------------------

def test_parse_data_record_event():
    r = protocol.parse_data_record(load("data_event.txt"))
    assert r["index"] == 1287
    assert r["time"] == "25-12-17 10:07:02"
    assert r["voltage"] == 53.124
    assert r["current"] == 0.856
    assert r["temperature"] == 30.0
    assert r["soc"] == 99
    assert r["base_state"] == "Charge"
    assert r["bat_events_raw"] == "0x2"
    assert r["bat_events"] == "BHV"
    assert r["power_events"] == ""
    assert r["system_fault"] == "0x0"
    assert r["alarm"] is True             # a Bat Event is set
    assert len(r["cells"]) == 15
    assert r["cells"][4]["volt_state"] == "High"


def test_parse_data_record_syserr():
    r = protocol.parse_data_record(load("data_event_syserr.txt"))
    assert r["index"] == 1286
    assert r["base_state"] == "SysError"
    assert r["power_events_raw"] == "0x10000000"
    assert r["power_events"] == "SYSERR"
    assert r["system_fault"] == "0x200"
    assert r["alarm"] is True


def test_parse_data_record_history():
    r = protocol.parse_data_record(load("data_history.txt"))
    assert r["index"] == 1796            # "Rec Item Index"
    assert r["current"] == -1.706
    assert r["base_state"] == "Dischg"
    assert r["alarm"] is False           # all zero events, normal state


def test_wake_frame():
    assert protocol.WAKE_FRAME.hex().upper() == "7E32303031343638324330303438353230464343330D"
    assert len(protocol.WAKE_FRAME) == 22


def test_format_set_time():
    import datetime

    dt = datetime.datetime(2026, 8, 31, 21, 40, 17)
    # 2-digit year, space separated, zero-padded (the form BatteryView sends)
    assert protocol.format_set_time(dt) == "time 26 08 31 21 40 17"
    assert protocol.command_ok("Test result:pass\r\nCommand completed successfully\r\n$$")


def test_data_record_time_valid_flag():
    assert protocol.parse_data_record(load("data_event.txt"))["time_valid"] is True
    # RTC lost after a full power-down -> bogus timestamp
    bogus = load("data_event.txt").replace("25-12-17 10:07:02", "00-00-00 00:00:00")
    assert protocol.parse_data_record(bogus)["time_valid"] is False
    bogus2 = load("data_event.txt").replace("25-12-17 10:07:02", "24-00-00 01:00:10")
    assert protocol.parse_data_record(bogus2)["time_valid"] is False


# --- force balance session ---------------------------------------------

def _run_balance(end_deltas, start_deltas={1: 80, 2: 20}):
    from datetime import datetime, timedelta

    t = datetime(2026, 11, 15, 8, 0)
    s = {"state": "charging", "started": t}
    protocol.balance_step(s, t, 90, start_deltas)
    assert s["state"] == "charging"
    t += timedelta(hours=1)
    protocol.balance_step(s, t, 98, start_deltas)
    assert s["state"] == "holding" and s["start_delta_mv"] == start_deltas
    for _ in range(120):
        t += timedelta(minutes=1)
        protocol.balance_step(s, t, 100, end_deltas)
    return s


def test_balance_balanced():
    s = _run_balance({1: 25, 2: 10})
    assert s["state"] == "balanced"
    assert s["end_delta_mv"] == {1: 25, 2: 10}
    assert "module" not in s and "samples" not in s


def test_balance_good_progress():
    s = _run_balance({1: 50, 2: 10})
    assert s["state"] == "good_progress" and s["module"] == 1


def test_balance_cell_behind():
    s = _run_balance({1: 75, 2: 10})
    assert s["state"] == "cell_behind" and s["module"] == 1


def test_balance_hold_restarts_below_95():
    from datetime import datetime, timedelta

    t = datetime(2026, 11, 15, 8, 0)
    s = {"state": "charging", "started": t}
    protocol.balance_step(s, t, 99, {1: 80})
    protocol.balance_step(s, t + timedelta(minutes=30), 96, {1: 70})
    assert s["state"] == "holding"
    protocol.balance_step(s, t + timedelta(minutes=31), 94, {1: 70})
    assert s["state"] == "charging" and "hold_started" not in s


def test_balance_times_out():
    from datetime import datetime, timedelta

    t = datetime(2026, 11, 15, 8, 0)
    s = {"state": "charging", "started": t}
    protocol.balance_step(s, t + timedelta(hours=49), 90, {1: 80})
    assert s["state"] == "timed_out"

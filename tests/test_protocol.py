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


def test_balance_session_survives_json_round_trip():
    import json
    from datetime import datetime, timedelta, timezone

    t = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
    s = {"state": "charging", "started": t}
    protocol.balance_step(s, t, 99, {1: 80, 2: 20})
    protocol.balance_step(s, t + timedelta(minutes=1), 99, {1: 78, 2: 20})
    assert s["state"] == "holding" and s["samples"]

    restored = protocol.balance_load(json.loads(json.dumps(protocol.balance_dump(s))))
    assert restored == s
    # and the restored session keeps running
    for minute in range(2, 122):
        protocol.balance_step(restored, t + timedelta(minutes=minute), 100, {1: 25, 2: 10})
    assert restored["state"] == "balanced"


def test_balance_load_rejects_junk():
    assert protocol.balance_load(None) == {"state": "idle"}
    assert protocol.balance_load({"state": "nonsense"}) == {"state": "idle"}


# --- pwr alarms -------------------------------------------------------------

def test_pwr_alarms_split_by_kind():
    mods = protocol.parse_pwr(load("pwr.txt"))
    assert mods[1]["voltage_alarm"] is False
    assert mods[1]["current_alarm"] is False
    assert mods[1]["temperature_alarm"] is False

    # module 2: cell-voltage state High, everything else Normal
    lines = load("pwr.txt").splitlines()
    row = next(i for i, l in enumerate(lines) if l.startswith("2 "))
    head, tail = lines[row].split("2026-09-01", 1)
    lines[row] = head + "2026-09-01" + tail.replace("Normal", "High", 1)
    mods = protocol.parse_pwr("\n".join(lines))
    assert mods[2]["bv_state"] == "High"
    assert mods[2]["voltage_alarm"] is True
    assert mods[2]["current_alarm"] is False
    assert mods[2]["temperature_alarm"] is False
    assert mods[2]["problem"] is True


# --- energy / time remaining ------------------------------------------------

def test_integrate_energy_splits_charge_and_discharge():
    totals = {"charged": 0.0, "discharged": 0.0}
    protocol.integrate_energy(totals, 1000, 1000, 3600, 7200)
    assert totals == {"charged": 1000.0, "discharged": 0.0}
    protocol.integrate_energy(totals, -500, -1500, 1800, 7200)
    assert totals == {"charged": 1000.0, "discharged": 500.0}


def test_integrate_energy_skips_gaps_and_missing_readings():
    totals = {"charged": 0.0, "discharged": 0.0}
    protocol.integrate_energy(totals, None, 1000, 60, 300)   # first poll
    protocol.integrate_energy(totals, 1000, None, 60, 300)   # no reading
    protocol.integrate_energy(totals, 1000, 1000, 301, 300)  # gap too long
    protocol.integrate_energy(totals, 1000, 1000, 0, 300)
    assert totals == {"charged": 0.0, "discharged": 0.0}


def test_time_remaining():
    assert protocol.time_remaining(50.0, 100.0, 10.0) == (5.0, None)
    assert protocol.time_remaining(50.0, 100.0, -25.0) == (None, 2.0)
    assert protocol.time_remaining(50.0, 100.0, 0.1) == (None, None)
    assert protocol.time_remaining(None, 100.0, 10.0) == (None, None)
    # no full-charge capacity (high-voltage stack): only time to empty
    assert protocol.time_remaining(50.0, None, 10.0) == (None, None)
    assert protocol.time_remaining(50.0, None, -10.0) == (None, 5.0)


# --- automatic full-charge tracking -----------------------------------------

_T0 = 1_800_000_000.0


def _full_charge(tracker, start, minutes, soc, deltas):
    for minute in range(minutes + 1):
        protocol.track_full_charge(tracker, start + minute * 60, soc, deltas)


def test_tracking_no_full_charge_yet():
    tracker = {}
    protocol.track_full_charge(tracker, _T0, 60, {1: 20})
    summary = protocol.balance_summary(tracker, _T0)
    assert summary["state"] == "balance_recommended"
    assert summary["score"] is None and summary["full_charges_30d"] == 0


def test_tracking_balanced_window_open_then_closed():
    tracker = {}
    protocol.track_full_charge(tracker, _T0, 98, {1: 60, 2: 20})
    _full_charge(tracker, _T0 + 60, 180, 100, {1: 22, 2: 10})
    summary = protocol.balance_summary(tracker, _T0 + 181 * 60)
    assert summary["at_full_charge"] is True
    assert summary["state"] == "balanced" and summary["score"] == 100
    assert summary["start_delta_mv"] == {"1": 60, "2": 20}
    assert summary["end_delta_mv"] == {"1": 22, "2": 10}
    assert summary["worst_module"] == 1

    end = _T0 + 181 * 60
    protocol.track_full_charge(tracker, end + 60, 94, {1: 30, 2: 12})
    assert tracker["window"] is None and len(tracker["history"]) == 1
    summary = protocol.balance_summary(tracker, end + 120)
    assert summary["at_full_charge"] is False
    assert summary["state"] == "balanced"
    assert summary["last_full_charge"] == end
    assert summary["duration_minutes"] == 181


def test_tracking_under_watch_and_weak_cell():
    # short full charge, spread not closed: under watch
    tracker = {}
    _full_charge(tracker, _T0, 30, 99, {1: 120})
    assert protocol.balance_summary(tracker, _T0 + 1800)["state"] == "under_watch"
    # the same spread after more than two hours at full: weak cell
    _full_charge(tracker, _T0 + 1860, 120, 99, {1: 120})
    summary = protocol.balance_summary(tracker, _T0 + 3 * 3600)
    assert summary["state"] == "weak_cell_suspected"
    assert summary["score"] == protocol.balance_score(120) == 47


def test_tracking_stale_after_two_weeks_and_history_pruned():
    tracker = {}
    _full_charge(tracker, _T0, 60, 100, {1: 10})
    protocol.track_full_charge(tracker, _T0 + 3660, 90, {1: 10})
    assert protocol.balance_summary(tracker, _T0 + 13 * 86400)["state"] == "balanced"
    assert protocol.balance_summary(tracker, _T0 + 15 * 86400)["state"] == "balance_recommended"
    # a window that closes 40 days later drops the old one from the history
    late = _T0 + 40 * 86400
    _full_charge(tracker, late, 10, 100, {1: 10})
    protocol.track_full_charge(tracker, late + 660, 90, {1: 10})
    assert [h["started"] for h in tracker["history"]] == [late]


def test_tracking_is_json_serialisable():
    import json

    tracker = {}
    _full_charge(tracker, _T0, 5, 100, {1: 10, 2: 15})
    assert json.loads(json.dumps(tracker)) == tracker


def test_balance_score_range():
    assert protocol.balance_score(0) == 100
    assert protocol.balance_score(30) == 100
    assert protocol.balance_score(115) == 50
    assert protocol.balance_score(200) == 0
    assert protocol.balance_score(500) == 0


# --- high-voltage stacks (experimental) -------------------------------------
#
# Fixtures in fixtures/hv/ are real captures from
# mletenay/home-assistant-pylontech (MIT): an SC0500-style unit on firmware
# B50 (4 modules) and one on B52.12.0 (8 modules).

def load_hv(name: str) -> str:
    return (FIX / "hv" / name).read_text(encoding="utf-8")


def test_hv_detection():
    assert protocol.is_hv_pwr(load_hv("B50_pwr.txt")) is True
    assert protocol.is_hv_pwr(load_hv("B52.12.0_pwr.txt")) is True
    assert protocol.is_hv_pwr(load("pwr.txt")) is False
    assert protocol.is_hv_pwr("") is False
    # the low-voltage parser finds nothing in a high-voltage reply
    assert protocol.parse_pwr(load_hv("B50_pwr.txt")) == {}


def test_hv_pwr_b50():
    d = protocol.parse_pwr_hv(load_hv("B50_pwr.txt"))
    assert d["voltage"] == 197.958
    assert d["current"] == -0.144
    assert d["power"] == -28.5
    assert d["soc"] == 15
    assert d["remaining_capacity"] == 7.621
    assert d["cell_voltage_min"] == 3.238 and d["cell_voltage_max"] == 3.305
    assert d["cell_voltage_delta"] == 67
    assert d["temperature_min"] == 17.0 and d["temperature_max"] == 20.0
    assert d["temperature_avg"] == 18.6
    assert d["base_state"] == "Dischg"
    assert d["error_code"] == "0x0"
    assert d["charging"] is False and d["problem"] is False


def test_hv_pwr_b52_has_extra_wh_column():
    d = protocol.parse_pwr_hv(load_hv("B52.12.0_pwr.txt"))
    assert d["voltage"] == 394.349
    assert d["current"] == 0.0
    assert d["soc"] == 20 and d["remaining_capacity"] == 10.0
    assert d["cell_voltage_delta"] == 63
    assert d["temperature_avg"] == 20.9
    assert d["base_state"] == "Idle"
    assert d["timestamp"] == "2025-12-05 05:10:18"
    assert d["bv_state"] == "Normal" and d["error_code"] == "0x0"


def test_hv_pwr_error_code_is_a_problem():
    raw = load_hv("B50_pwr.txt").replace("0x0", "0x20")
    d = protocol.parse_pwr_hv(raw)
    assert d["error_code"] == "0x20" and d["problem"] is True


def test_hv_unit():
    mods = protocol.parse_unit_hv(load_hv("B50_unit.txt"))
    assert sorted(mods) == [1, 2, 3, 4]
    m = mods[3]
    assert m["voltage"] == 49.388 and m["current"] == -0.144
    assert m["temperature"] == 26.0
    assert m["cell_temp_min"] == 18.0 and m["cell_temp_max"] == 19.0
    assert m["cell_voltage_min"] == 3.238 and m["cell_voltage_delta"] == 66
    assert m["soc"] == 15 and m["remaining_capacity"] == 7.621
    assert m["base_state"] == "Dischg"
    assert m["problem"] is False and m["voltage_alarm"] is False

    mods = protocol.parse_unit_hv(load_hv("B52.12.0_unit.txt"))
    assert sorted(mods) == list(range(1, 9))
    assert mods[8]["voltage"] == 49.395 and mods[8]["soc"] == 59
    assert mods[1]["timestamp"] == "2025-12-05 05:10:38"


def test_hv_unit_feeds_cell_health():
    m = protocol.parse_unit_hv(load_hv("B52.12.0_unit.txt"))[1]
    verdict, condition = protocol.assess_cell_health(
        m["cell_voltage_delta"], m["current"], m["soc"], m["temperature"]
    )
    assert (verdict, condition) == ("failed", "idle")  # 58 mV at rest


def test_hv_bat_split_per_module():
    cells = protocol.parse_bat_hv(load_hv("B52.12.0_bat.txt"), 8)
    assert sorted(cells) == list(range(1, 9))
    assert all(len(c) == 15 for c in cells.values())
    assert cells[1][0] == {"index": 0, "voltage": 3.264, "balancing": False}
    assert cells[1][14]["index"] == 14
    # module 1's weakest cell matches the `unit` row's lowest cell voltage
    low = min(c["voltage"] for c in cells[1])
    assert low == protocol.parse_unit_hv(load_hv("B52.12.0_unit.txt"))[1]["cell_voltage_min"]

    cells = protocol.parse_bat_hv(load_hv("B50_bat.txt"), 4)
    assert [len(c) for c in cells.values()] == [12, 12, 12, 12]
    # a cell count that does not divide by the module count is refused
    assert protocol.parse_bat_hv(load_hv("B50_bat.txt"), 5) == {}
    assert protocol.parse_bat_hv("", 4) == {}


def test_hv_info():
    d = protocol.parse_info(load_hv("B52.12.0_info.txt"))
    assert d["model"] == "CMU_A"
    assert d["specification"] == "384V/50AH"
    assert d["main_soft_version"] == "B52.12.0"
    assert d["cell_count"] == 120
    assert d["barcode"] == "PPTAP01210108048"  # falls back to "Module Barcode"

    bmus = protocol.parse_info_bmus(load_hv("B52.12.0_info.txt"))
    assert sorted(bmus) == list(range(8))
    assert bmus[7] == "PPTAP01A20428669"
    assert protocol.parse_info_bmus(load("info.txt")) == {}

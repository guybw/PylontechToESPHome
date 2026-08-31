# PylontechToESPHome

Monitor a **Pylontech** lithium battery stack (US2000 / US2000B / US2KBPL /
US3000 / US3000C / US5000, "Type C" console) in **Home Assistant** over a cheap
**ESP8266** running **ESPHome** — full per-module and per-cell data, health
assessment, and the on-device event log.

---

## How it works

The battery has an RS232 **console** port that answers text commands
(`pwrsys`, `pwr`, `bat`, `info`, `stat`, `data event` …). This project reads
that console.

**The new way (this repo):**

```
Pylontech console ──serial── ESP8266 (ESPHome serial_proxy) ──WiFi/API── Home Assistant
                              "dumb bridge", no parsing        "Pylontech" integration
                                                               parses + creates entities
```

- The ESP is a **transparent serial bridge** — it does no parsing, just streams
  the console over the ESPHome Native API (`serial_proxy`).
- A **Home Assistant custom integration** (`custom_components/pylontech/`) opens
  that stream, sends the commands, parses the replies, and creates all the
  entities.

**The old way:** the ESP parsed the console itself in ESPHome C++ lambdas and
published a fixed set of sensors. It worked but was hard-coded to a battery
count, brittle to firmware differences, and had no event log, health verdict,
per-cell data, or clock sync. See [Legacy](#legacy-the-old-way) below. Prefer
the new way.

---

## What you need

| | |
|---|---|
| **ESP8266** | An ESP-01S is enough. The author sells a ready-made board + Pylontech shield + cable + case (UK, £20 — email `guybw` at `hotmail` dot `com`). |
| **Serial link** | The Pylontech "Console" port to the ESP UART. The shield does the RS232↔TTL level shift; otherwise use a MAX3232 adapter. Console is **115200 8N1**. |
| **ESPHome ≥ 2026.3.0** | For the `serial_proxy` component. The Home Assistant *ESPHome Device Builder* add-on is new enough. |
| **Home Assistant** | Any reasonably current version. HACS optional but easiest. |

---

## Part 1 — Flash the ESPHome bridge

1. Take [`pylontech_example.yaml`](pylontech_example.yaml) as your starting
   point (copy it into your ESPHome config as e.g. `pylontech.yaml`).
2. Create a `secrets.yaml` next to it:

   ```yaml
   wifi_ssid: "YourWiFi"
   wifi_password: "YourWiFiPassword"
   pylontech_api_key: "<32-byte base64 key>"     # ESPHome generates one for you
   pylontech_ota_password: "SomethingLong"
   ```

3. Set `manual_ip` to a **free static IP** on your LAN (the integration needs a
   stable address).
4. Install / flash — **USB for the first flash**, OTA after that.
5. Write down the **device IP** and the **API encryption key** — you enter both
   in Home Assistant next.

The whole bridge config is ~40 lines; the important parts:

```yaml
uart:
  id: uart_bus
  tx_pin: GPIO1
  rx_pin: GPIO3
  baud_rate: 115200
  rx_buffer_size: 4096          # the `pwr` reply is a ~2.6 KB burst; 2048 overruns it

serial_proxy:
  - id: pylontech_console
    uart_id: uart_bus
    name: "Pylontech Console"   # must match "Serial port name" in Home Assistant
    port_type: TTL
```

> `serial_proxy` is an official but still **experimental** ESPHome component.
> If it won't fit your ESP, see [Troubleshooting](#troubleshooting).

---

## Part 2 — Install the Home Assistant integration

**HACS (recommended)**

1. HACS → ⋮ → *Custom repositories* → add this repo, category **Integration**.
2. Install **Pylontech (ESPHome serial bridge)**, restart Home Assistant.

**Manual**

Copy `custom_components/pylontech/` into your HA `config/custom_components/` and
restart.

---

## Part 3 — Add it in Home Assistant

**Settings → Devices & services → Add integration → “Pylontech (ESPHome serial
bridge)”**, then enter:

| Field | Value |
|---|---|
| **Host** | the ESP's IP, e.g. `192.168.1.50` |
| **API encryption key** | the `key:` under `api: → encryption:` in the ESPHome config (same as `pylontech_api_key` in `secrets.yaml`) |
| **Port** | `6053` (default) |
| **Serial port name** | `Pylontech Console` (must match `name:` on the `serial_proxy`) |

That's it — nothing else is configured on the ESP.

---

## What you get

A **Pylontech** stack device, plus **one device per battery module** (each
shows its own model / spec / firmware / serial — mixed stacks are fine).

**Stack**

- State of charge, State of health
- Voltage, Current, Power
- Remaining / Full-charge capacity
- Min / Average / Max cell voltage, **Cell voltage delta (mV)**
- Min / Average / Max temperature
- Charge cycles
- **Cell health** — `healthy` / `degrading` / `failed` / `unknown`
- Last update
- Binary: **Charging**, **Problem**

**Per module**

- Voltage, Current, Power, State of charge, Temperature
- Min / Max cell voltage & temperature, MOSFET temperature
- State (`Charge` / `Dischg` / `Idle` …)
- **Cell health** (with `spread_mv` / `condition` attributes)
- Binary: **Problem**

**Cell health** uses the cell-voltage spread, only judged when conditions are
valid (enough load or a clean idle plateau, sensible SoC, not cold) — the
method from [simonpasley/pylontech-battery-health](https://github.com/simonpasley/pylontech-battery-health).

---

## Options

**Settings → Devices & services → Pylontech → Configure**

| Option | Default | Notes |
|---|---|---|
| **Poll interval** | 60 s | `pwrsys` + `pwr` every interval; `stat` every 30 min; `info` once. |
| **Per-cell sensors** | off | Adds a voltage sensor for every cell (15/module), a **Weakest cell** sensor, and a **Balancing** binary sensor per module. One extra `bat N` per module per poll. Turning it back off removes those entities. |
| **Sync battery clock** | off | Sets the battery RTC to Home Assistant's time on every connect (see below). |

---

## Services

**`pylontech.get_log`** — read the battery's on-device log (Developer Tools →
Actions, or `response_variable:` in a script/automation).

| Field | |
|---|---|
| `source` | `event` (faults / state changes) or `history` (periodic samples) |
| `count` | 1–50 recent records |
| `config_entry_id` | only if you have more than one stack |

Each record: index, time, `time_valid`, pack V/I/T/SoC, state flags, decoded
`power_events` / `bat_events` / `system_fault`, an **`alarm`** boolean, and a
per-cell snapshot.

**`pylontech.set_time`** — set the battery RTC (optional `datetime`, defaults to
now). The console's `time` command only accepts a **2-digit year**
(`time <yy> <MM> <dd> <HH> <mm> <ss>`) — the form the OEM *BatteryView* tool
uses. Only affects **future** log timestamps; the RTC otherwise loses time on a
full power-down.

---

## Troubleshooting

**Battery returns no data at all.** After a full power-off some batteries need a
wake-up frame at **1200 baud** before the 115200 console responds:

```
7E 32 30 30 31 34 36 38 32 43 30 30 34 38 35 32 30 46 43 43 33 0D
```

Send it once with a USB-serial adapter at 1200 baud (the classic method).

**`pwrsys` / `pwr` return nothing.** They need debug mode — the integration
sends `login debug` on every connect automatically. If you're poking the
console by hand, send `login debug` first.

**`serial_proxy` won't compile / won't fit the ESP-01S.** Swap the
`serial_proxy:` block for the third-party raw-TCP bridge
[`oxan/esphome-stream-server`](https://github.com/oxan/esphome-stream-server):

```yaml
external_components:
  - source: github://oxan/esphome-stream-server
stream_server:
  uart_id: uart_bus
  port: 6638
```

…and reach it with `ncat <ip> 6638`. Note the **Home Assistant integration
needs `serial_proxy`** — `stream_server` is only for manual console access.

**Log timestamps are wrong / show `00-00-00`.** The RTC lost power. Use
*Sync battery clock* or `pylontech.set_time` — it fixes new entries only.

**`SysError` / `System Fault 0x200` in the log.** `0x200` is an inter-module
communication fault (link cable between packs). Isolated entries right after a
power-up are the normal Pylontech power-on handshake; frequent ones during
running mean a marginal link cable.

**Poking the console directly.** Any `aioesphomeapi` client can open the
`Pylontech Console` serial port. Useful commands: `info`, `info N`, `pwrsys`,
`pwr`, `bat N`, `stat`, `data event [i]`, `data history [i]`, `help`, `time`,
`logout`.

---

## Legacy (the old way)

The original approach was a single ESPHome YAML that parsed the console in C++
lambdas and published sensors straight from the ESP — no Home Assistant
integration required. Two variants existed: one for a single battery, one
hard-coded for up to 8 modules.

It still works, but it's frozen. Pull the old files from git history if you
want them:

```
git log --oneline            # find the last commit before the rewrite
git show <commit>:pytlontech.yaml
git show <commit>:pylontech-8-battery.yaml
```

The new bridge + integration replaces it with dynamic module/cell discovery,
mixed-stack support, health assessment, the event log, and clock sync. If you
were running the old YAML, move to
[Part 1](#part-1--flash-the-esphome-bridge).

---

## Credits

- [irekzielinski/Pylontech-Battery-Monitoring](https://github.com/irekzielinski/Pylontech-Battery-Monitoring)
  — the original console reverse-engineering this is built on.
- [simonpasley/pylontech-battery-health](https://github.com/simonpasley/pylontech-battery-health)
  — excellent standalone health tool (runs on Windows now); the cell-spread
  verdict here follows its method. If a pack is misbehaving, run it.

Got it working? Please open an issue and say so.

---

<sub>

**Keywords:** Pylontech Home Assistant integration · Pylontech ESPHome · Pylontech
US2000 US2000B US2000C US2KBPL US3000 US3000C US5000 Home Assistant · Pylontech
console RS232 reader · Pylontech BMS monitoring · read Pylontech cell voltages
Home Assistant · Pylontech state of health SoH · Pylontech cycle count · Pylontech
event log / history log · Pylontech `pwrsys` `pwr` `bat` `stat` `info` commands ·
Pylontech `data event` parser · ESPHome `serial_proxy` example · ESP8266 ESP-01S
Pylontech shield · Pylontech serial to WiFi bridge · Pylontech `login debug` ·
Pylontech `pwrsys` returns nothing · Pylontech battery not responding 1200 baud
wake-up · Pylontech mixed stack US3000C + US2000B · Pylontech cell imbalance /
cell voltage delta / cell balancing · Pylontech SysError System Fault 0x200 ·
Pylontech set time / RTC / DS3231 · Pylontech BatteryView alternative · Pylontech
Type C protocol · Pylontech aioesphomeapi · Pylontech HACS · monitor Pylontech
without Victron / without CAN bus · Pylontech per-cell monitoring Home Assistant.

</sub>

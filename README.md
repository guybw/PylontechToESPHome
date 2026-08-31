# PylontechToESPHome

Monitor a **Pylontech** lithium battery stack in **Home Assistant** over a cheap
**ESP8266** running **ESPHome** — full per-module and per-cell data, health
assessment, and the on-device event log.

Works with any Pylontech that has a **"Console" port** and speaks the "Type C"
text protocol — US2000B / US2000C / US2KBPL / US3000 / US3000C / US5000 and
similar. If your battery has a socket labelled *Console* on the BMS, you're in.

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

**The old way (`legacy/`):** the ESP parsed the console itself in ESPHome C++
lambdas and published a fixed set of sensors. It worked but was hard-coded to a
battery count, brittle to firmware differences, and had no event log, health
verdict, per-cell data, or clock sync. See [Legacy](#legacy-the-old-way) below.
Prefer the new way.

---

## What you need

| | |
|---|---|
| **ESP8266** | An ESP-01S is enough. The author sells a ready-made board + Pylontech shield + cable + case, **pre-flashed** (UK, £20 — email `guybw` at `hotmail` dot `com`). With one of those, skip most of Part 1. |
| **Serial link** | The Pylontech *Console* port to the ESP UART: battery TX → ESP RX, battery RX → ESP TX, GND → GND. The shield does the RS232↔TTL level shift; DIY, use a MAX3232 module. Console-port RJ pinout is documented in [irekzielinski's repo](https://github.com/irekzielinski/Pylontech-Battery-Monitoring). Console is **115200 8N1**. |
| **ESPHome** | Version **≥ 2026.3.0** (for `serial_proxy`). The Home Assistant *ESPHome Device Builder* add-on auto-updates, so it's fine — see Part 1 if you don't have it yet. |
| **Home Assistant** | Any reasonably current version. [HACS](https://hacs.xyz) makes installing this integration a click; manual copy works too. |

---

## Part 1 — Flash the ESPHome bridge

*(Using a pre-flashed board from the author? It's already done — jump to
Part 2.)*

**Get ESPHome, if you don't have it:** in Home Assistant, **Settings →
Add-ons → Add-on Store → ESPHome Device Builder → Install → Start**, then
**Open Web UI**.

1. In the ESPHome dashboard, **New device** → name it `pylontech` → pick
   *ESP8266*. It creates `pylontech.yaml` (with a generated API encryption
   key inside — **keep that key line**).
2. **Edit** that `pylontech.yaml` and replace everything *except the
   `api:` / `encryption:` / `key:` line* with the contents of
   [`pylontech_example.yaml`](pylontech_example.yaml) from this repo.
3. Open **`secrets.yaml`** (top-right ⋮ menu → *Secrets* in the dashboard) and
   add:

   ```yaml
   wifi_ssid: "YourWiFi"
   wifi_password: "YourWiFiPassword"
   pylontech_ota_password: "SomethingLong"
   ```

   (The example references `!secret pylontech_api_key` too — either add your
   generated key there, or paste the key straight into `pylontech.yaml` and
   delete that `!secret` line.)
4. Set `manual_ip` in the YAML to a **free static IP** on your LAN — the
   integration needs a stable address.
5. **Install:**
   - *First time:* **Plug into this computer** (USB). A bare ESP-01S needs a
     USB-serial adapter with **GPIO0 held to GND** to enter programming mode —
     this is the fiddly bit.
   - *After that:* **Wirelessly (OTA)** — no cable.
6. Note the device's **IP** and its **API encryption key** (the `key:` value) —
   you enter both in Home Assistant next.

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

1. HACS → ⋮ (top-right) → *Custom repositories*.
2. Repository: `https://github.com/guybw/PylontechToESPHome` · Type:
   **Integration** · Add.
3. Find **Pylontech (ESPHome serial bridge)** in the list, **Download**, then
   restart Home Assistant.

**Manual**

Copy the `custom_components/pylontech/` folder from this repo into your Home
Assistant `config/custom_components/` folder, then restart.

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

**Did it work?** Within a minute you should see a new **Pylontech** device with
~15 sensors (State of charge, Voltage, Power, Cell health …) plus one **Battery
N** sub-device per module. If it says *"Failed to connect"*, check the IP and
that the encryption key matches exactly; if the device appears but everything
is *Unavailable*, the ESP isn't talking to the battery yet — see
[Troubleshooting](#troubleshooting).

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

**`pylontech.wake`** — send the 1200-baud wake frame a silent battery needs
after a full power-off, then switch back to 115200. Runs automatically after a
few failed polls; this is the manual trigger.

---

## Troubleshooting

**Battery returns no data at all.** After a full power-off some batteries stay
silent on the 115200 console until they get a wake frame at **1200 baud**
(`~20014682C0048520FCC3\r`). The integration handles this itself — after a few
failed polls it drops the console to 1200 baud via `serial_proxy`, sends the
frame, and switches back. You can also trigger it manually with the
**`pylontech.wake`** service. The classic USB-serial-adapter-at-1200-baud
method still works too.

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

---

## For tinkerers — talk to the console yourself

Any `aioesphomeapi` client can open the `Pylontech Console` serial port directly
(the HA integration must be stopped first — the port allows one client at a
time). Useful commands: `info`, `info N`, `pwrsys`, `pwr`, `bat N`, `stat`,
`data event [i]`, `data history [i]`, `help`, `time`, `logout`. Send
`login debug` first or most commands return nothing.

---

## Legacy (the old way)

`legacy/` holds the original approach: a single ESPHome YAML that parsed the
console in C++ lambdas and published sensors straight from the ESP — no Home
Assistant integration required.

- `legacy/pytlontech.yaml` — single battery
- `legacy/pylontech-8-battery.yaml` — up to 8 modules (hard-coded)
- `legacy/README.md` — the original project docs

It still works, but it's frozen. The new bridge + integration replaces it with
dynamic module/cell discovery, mixed-stack support, health assessment, the
event log, and clock sync. If you were running the old YAML, move to
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

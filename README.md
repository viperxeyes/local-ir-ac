# Local IR AC for Home Assistant

Turn a **Tuya Wi-Fi IR blaster** into a proper `climate` entity — fully local, no cloud at runtime.

This integration was written for air conditioners whose remote has no published code database: it replays **raw IR frames you captured once** from the physical remote, mapped onto Home Assistant's standard climate surface. It was built against a Moes S11 RF/IR blaster (Tuya protocol 3.5) driving a split AC whose remote has cool / auto / dry / heat and a separate *speed* (turbo) button.

> **Status:** works on the hardware it was written for, and is deliberately small. It is not a general IR library and ships no brand codes.

## What it does

| Home Assistant mode | IR frame sent | Fan | Temperature |
|---|---|---|---|
| `cool` | `M0_T<temp>_S<fan>` | auto / low / medium / high | yes |
| `heat_cool` (the unit's "auto") | `M2_S<fan>` | auto / low / medium / high | no |
| `dry` | `M4_S0` | auto only | no |
| `heat` | `heat_mode` | fixed | — |
| `fan_only` (the unit's "speed"/turbo) | `speed_mode` | auto only | no |
| `off` | `power_off` | — | — |

Two behaviours matter and are built in:

* **Wake rule.** The unit ignores every frame except the one learned at `23 °C` while it is off, so power-on always sends the 23 °C cool frame first, waits 0.8 s, then sends the requested mode.
* **De-duplication.** The same frame is never sent twice in a row, which stops the unit double-beeping when only the setpoint is re-asserted.

`heat_cool` is used for the unit's *auto* mode because Matter controllers (Apple Home, Alexa) only expose an Auto button when a thermostat advertises heating **and** cooling.

## How the codes are captured

IR codes are learned from the physical remote and stored in a JSON file. The workflow used here:

1. In Home Assistant, arm the blaster's learn command — `remote.learn_command` with `device: "<remote>"` and `command: ["<code_name>"]` — and press the button on the remote.
2. The captured frame is stored by the Tuya local integration under `/config/.storage/tuya_local_remote_<device_id>_codes` as base64.
3. Decode it to hex and add it to the codes file under the name this integration expects.

Pressing every combination by hand is tedious, so only the codes you actually use need capturing. Anything missing is logged as `IR code <name> not found` and the entity keeps its previous state.

## Installation

### HACS (recommended)

1. HACS → **⋮ → Custom repositories**
2. Repository: `https://github.com/viperxeyes/local-ir-ac` — category **Integration**
3. Install it, then **restart Home Assistant** (a reload is not enough — platform code is imported at startup).

### Manual

Copy `custom_components/local_ir_ac/` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

YAML only, in `configuration.yaml`:

```yaml
climate:
  - platform: local_ir_ac
    name: Living Room AC
    host: 192.168.1.128          # the IR blaster, not the AC
    device_id: bf5e05d4a77f7d1767sese
    local_key: 0123456789abcdef
    codes_file: /config/local_ir_codes.json   # default
    min_temp: 20
    max_temp: 26
    device: AC                   # unique_id suffix; use one per climate entity
```

`device_id` and `local_key` belong to the **blaster** and come from the Tuya IoT platform (`GET /v1.0/devices/{id}`) or `python -m tinytuya wizard`. Re-pairing the blaster in Smart Life rotates the key — update it here if the entity stops responding.

## Codes file

`local_ir_codes.json` maps a name to a hex-encoded IR frame:

```json
{
  "power_off": "aabbcc...",
  "M0_T23_S0": "aabbcc...",
  "M2_S0": "aabbcc...",
  "M4_S0": "aabbcc...",
  "speed_mode": "aabbcc...",
  "heat_mode": "aabbcc..."
}
```

A list of `{"name": ..., "code": ...}` objects is also accepted.

## Limitations, stated plainly

* **Optimistic only.** IR is one-way; the entity shows what it last sent and cannot see a change made with the physical remote.
* **No current temperature.** There is no sensor; `current_temperature` is `None`.
* **Codes are per-remote.** A different AC make or model needs its own frames.
* **Protocol 3.5 only** (`tinytuya` `version=3.5`, `control_type=1`). Gateway-class blasters also refuse status queries; this integration never asks for state, which is why it works where polling integrations report the device as unavailable.
* **Fixed mode map.** The frame naming above is this author's remote; adapting it to another remote means editing `_code_name()`.

## Why not just use a generic IR integration?

Because the sequence logic is the whole problem. A generic `remote` entity can blast a frame, but it has no idea that the unit must be woken at 23 °C first, that mode/fan/setpoint share one frame, or that the turbo button belongs in the `fan_only` slot. Those rules are here, in about 250 lines, and they are what makes the AC usable from Apple Home, Alexa and the Watch.

## License

MIT — see [LICENSE](LICENSE).

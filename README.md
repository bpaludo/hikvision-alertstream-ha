# hikvision-alertstream-ha

A lightweight Docker-based bridge that connects Hikvision access control
devices (K1T, K2 series) to Home Assistant via MQTT, using the native
ISAPI alertStream endpoint.

## Author & Contributing

This project was created by **Bruno Paludo** ([@bpaludo](https://github.com/bpaludo)),
a home automation enthusiast and **not a professional developer**.
The code works well for the confirmed devices listed above, but there is
plenty of room for improvement.

**Contributors are more than welcome.** If you want to improve the code,
add support for new devices, improve documentation, or fix bugs — please
open a Pull Request. If you find an issue or have a question, open an Issue
and I will do my best to help, even if my response time may vary.

If you have a compatible device not listed above, please open an issue with
your device model and firmware version so we can document it.

## Why this exists

Hikvision's video intercom addon for Home Assistant
([pergolafabio/Hikvision-Addons](https://github.com/pergolafabio/Hikvision-Addons))
works great for video doorbell devices (KV/KD series). However, access
control terminals like the **DS-K1T344MBWX** are a different product line —
they don't speak the intercom SDK protocol. Their only external event
interface is the ISAPI alertStream, an HTTP multipart stream that delivers
access control events, face recognition results, door status changes, and more.

This bridge:
- Connects to the alertStream endpoint on the device
- Parses the multipart stream, skipping binary JPEG parts
- Publishes JSON events to MQTT topics
- Reconnects automatically on failure

## Confirmed compatible devices

- DS-K1T344MBWX (face recognition + QR code outdoor terminal)

Other devices in the K1T/K2 series that expose
`/ISAPI/Event/notification/alertStream` should work as well.
Feedback welcome.

## Requirements

- Docker and Docker Compose
- An MQTT broker (EMQX, Mosquitto, etc.)
- Home Assistant (any installation type)

## Quick start

**1. Clone the repository:**
```bash
git clone https://github.com/bpaludo/hikvision-alertstream-ha
cd hikvision-alertstream-ha
```

**2. Create your `.env` file:**
```bash
cp .env.example .env
nano .env
```

Fill in your device IP, credentials, and MQTT broker details.

**3. Start the container:**
```bash
docker compose up -d
```

**4. Check the logs:**
```bash
docker compose logs -f
```

You should see the alertStream connect and heartbeat events arriving.

## Important: .env file format

Do **NOT** use inline comments in your `.env` file.
The Docker `env_file` loader reads inline comments as part of the value,
which causes authentication failures.

❌ Wrong:
```
MQTT_HOST=192.168.1.10  # my broker
```

✅ Correct:
```
# my broker
MQTT_HOST=192.168.1.10
```

## MQTT topics

Events are published to two topics:

| Topic | Content |
|-------|---------|
| `hikvision/outdoor/event/{major}/{minor}` | Specific event by type |
| `hikvision/outdoor/event/any` | All events (catch-all) |

The base topic is configurable via `MQTT_TOPIC_BASE` in `.env`.

### Common event types

| major | minor | Description |
|-------|-------|-------------|
| 5 | 75 | Face recognition — authorized user |
| 5 | 76 | Face recognition — stranger |
| 1 | 0 | Door opened |
| 1 | 2 | Door locked |

The full payload is the raw JSON from the device, published as-is.
Face recognition events include `name` and `employeeNoString` fields.

### Example payload (face recognition)

```json
{
  "eventType": "AccessControllerEvent",
  "AccessControllerEvent": {
    "majorEventType": 5,
    "subEventType": 75,
    "name": "John Doe",
    "employeeNoString": "12345",
    "doorNo": 1
  }
}
```

## Home Assistant integration

### 1. REST commands (door control)

Add to your `configuration.yaml`:

```yaml
rest_command:
  hik_outdoor_open_door:
    url: "https://YOUR_DEVICE_IP/ISAPI/AccessControl/RemoteControl/door/1"
    method: PUT
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
    content_type: "application/xml"
    payload: "<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>"

  hik_outdoor_close_door:
    url: "https://YOUR_DEVICE_IP/ISAPI/AccessControl/RemoteControl/door/1"
    method: PUT
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
    content_type: "application/xml"
    payload: "<RemoteControlDoor><cmd>close</cmd></RemoteControlDoor>"

  hik_outdoor_door_always_open:
    url: "https://YOUR_DEVICE_IP/ISAPI/AccessControl/RemoteControl/door/1"
    method: PUT
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
    content_type: "application/xml"
    payload: "<RemoteControlDoor><cmd>alwaysOpen</cmd></RemoteControlDoor>"

  hik_outdoor_door_always_close:
    url: "https://YOUR_DEVICE_IP/ISAPI/AccessControl/RemoteControl/door/1"
    method: PUT
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
    content_type: "application/xml"
    payload: "<RemoteControlDoor><cmd>alwaysClose</cmd></RemoteControlDoor>"

  hik_outdoor_reboot:
    url: "https://YOUR_DEVICE_IP/ISAPI/System/reboot"
    method: PUT
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
```

Add to your `secrets.yaml`:

```yaml
hik_outdoor_user: admin
hik_outdoor_pass: YOUR_PASSWORD
```

### 2. REST sensors (device info)

```yaml
rest:
  - resource: "https://YOUR_DEVICE_IP/ISAPI/System/deviceInfo"
    username: !secret hik_outdoor_user
    password: !secret hik_outdoor_pass
    authentication: digest
    verify_ssl: false
    scan_interval: 300
    sensor:
      - name: "Outdoor Firmware"
        unique_id: outdoor_firmware
        value_template: "{{ value_json.DeviceInfo.firmwareVersion }}"
      - name: "Outdoor Model"
        unique_id: outdoor_model
        value_template: "{{ value_json.DeviceInfo.model }}"
      - name: "Outdoor Serial"
        unique_id: outdoor_serial
        value_template: "{{ value_json.DeviceInfo.serialNumber }}"
      - name: "Outdoor Firmware Date"
        unique_id: outdoor_firmware_date
        value_template: "{{ value_json.DeviceInfo.firmwareReleasedDate }}"
      - name: "Outdoor MAC"
        unique_id: outdoor_mac
        value_template: "{{ value_json.DeviceInfo.macAddress }}"
```

### 3. Automation example (doorbell notification with door action)

This automation uses the `call_state` sensor from the
[pergolafabio/Hikvision-Addons](https://github.com/pergolafabio/Hikvision-Addons)
addon, which must be installed and configured for your indoor station.

Tapping the notification opens the Home Assistant app on your doorbell
dashboard. The notification includes action buttons to open the door
directly without opening the app.

**Automation 1 — create two separate automations in Home Assistant.**
Paste each one individually in the YAML editor:

**Automation 1 — notification when doorbell rings:**
```yaml
alias: "Doorbell Ring - Phone Alert with Door Action"
trigger:
  - platform: state
    entity_id: sensor.indoor_call_state
    to: "ring"
condition: []
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "🔔 Someone at the door"
      message: "Someone is at the door"
      data:
        url: /lovelace/doorbell
        ttl: 0
        priority: high
        push:
          sound:
            name: default
            critical: 1
            volume: 1.0
        actions:
          - action: "OPEN_DOOR"
            title: "🔓 Open door"
          - action: "IGNORE"
            title: "Ignore"
```

**Automation 2 — open door when button is pressed on notification:**
```yaml
alias: "Doorbell - Handle Open Door action"
trigger:
  - platform: event
    event_type: mobile_app_notification_action
    event_data:
      action: "OPEN_DOOR"
action:
  - service: rest_command.hik_outdoor_open_door
```

> **Note:** Replace `sensor.indoor_call_state` with your actual entity name
> and `notify.mobile_app_your_phone` with your actual mobile app notify entity.

## Architecture

```
Hikvision K1T device
        │
        │  HTTPS (Digest Auth)
        │  /ISAPI/Event/notification/alertStream
        ▼
hikvision-alertstream (Docker)
        │
        │  MQTT
        ▼
EMQX / Mosquitto broker
        │
        │  MQTT discovery
        ▼
Home Assistant
```

## Configuration reference

See `.env.example` for all available options with descriptions.

## License

MIT

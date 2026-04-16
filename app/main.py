"""
hikvision-alertstream → MQTT bridge
Parser robusto que lida com parts binárias (JPEG) sem travar.
"""

import os
import json
import time
import logging
import re
import ssl
import threading

import requests
from requests.auth import HTTPDigestAuth
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("alertstream")

HIK_HOST               = os.environ["HIK_HOST"]
HIK_USER               = os.environ["HIK_USER"]
HIK_PASS               = os.environ["HIK_PASS"]
MQTT_HOST              = os.environ["MQTT_HOST"]
MQTT_PORT              = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USER              = os.environ["MQTT_USER"]
MQTT_PASS              = os.environ["MQTT_PASS"]
MQTT_TLS               = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_TOPIC_BASE        = os.environ.get("MQTT_TOPIC_BASE", "hikvision/outdoor/event")
MQTT_QOS               = int(os.environ.get("MQTT_QOS", 1))
MQTT_RETAIN_ANY        = os.environ.get("MQTT_RETAIN_ANY", "false").lower() == "true"
HEARTBEAT_LOG_INTERVAL = int(os.environ.get("HEARTBEAT_LOG_INTERVAL", 10))
RECONNECT_DELAY        = int(os.environ.get("RECONNECT_DELAY", 5))

ALERT_STREAM_URL = f"https://{HIK_HOST}/ISAPI/Event/notification/alertStream"

_mqtt_client = None
_mqtt_connected = threading.Event()


def _on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("MQTT conectado a %s:%s", MQTT_HOST, MQTT_PORT)
        _mqtt_connected.set()
    else:
        log.error("MQTT falhou ao conectar, rc=%s", rc)
        _mqtt_connected.clear()


def _on_disconnect(client, userdata, rc, properties=None, reasoncode=None):
    log.warning("MQTT desconectado (rc=%s) — reconectando...", rc)
    _mqtt_connected.clear()


def build_mqtt_client():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="hikvision-alertstream",
        clean_session=True,
    )
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    if MQTT_TLS:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def publish(topic, payload, retain=False):
    if not _mqtt_connected.wait(timeout=10):
        log.warning("MQTT indisponível, descartando: %s", topic)
        return
    _mqtt_client.publish(topic, payload, qos=MQTT_QOS, retain=retain)
    log.debug("MQTT → %s", topic)


def stream_parts(response):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    boundary = None
    raw = response.raw

    def read_line():
        line = b""
        while True:
            ch = raw.read(1)
            if not ch:
                return None
            line += ch
            if line.endswith(b"\r\n"):
                return line

    while True:
        line = read_line()
        if line is None:
            log.info("Stream encerrado pelo dispositivo.")
            return

        line_stripped = line.strip()

        if boundary is None:
            if line_stripped.startswith(b"--"):
                boundary = line_stripped
                log.debug("Boundary detectado: %s", boundary)
            continue

        if line_stripped != boundary and line_stripped != boundary + b"--":
            continue

        if line_stripped == boundary + b"--":
            log.info("Fim do stream multipart.")
            return

        headers = {}
        while True:
            hline = read_line()
            if hline is None:
                return
            hline_stripped = hline.strip()
            if not hline_stripped:
                break
            if b":" in hline_stripped:
                key, _, val = hline_stripped.partition(b":")
                headers[key.strip().lower().decode()] = val.strip().decode()

        content_type = headers.get("content-type", "")
        content_length = int(headers.get("content-length", 0))
        disposition = headers.get("content-disposition", "")

        name = ""
        m = re.search(r'name="([^"]+)"', disposition)
        if m:
            name = m.group(1)

        if content_type.startswith("image/"):
            if content_length > 0:
                raw.read(content_length)
                log.debug("Part binária ignorada: %s (%d bytes)", content_type, content_length)
            continue

        if content_length > 0:
            body_bytes = raw.read(content_length)
        else:
            body_bytes = b""
            while True:
                bline = read_line()
                if bline is None:
                    break
                if bline.strip() == boundary or bline.strip() == boundary + b"--":
                    break
                body_bytes += bline

        body = body_bytes.decode("utf-8", errors="replace").strip()
        yield name, content_type, body


_heartbeat_count = 0


def process_event(name, content_type, body):
    global _heartbeat_count

    if not body or name.lower() == "heartbeat":
        _heartbeat_count += 1
        if HEARTBEAT_LOG_INTERVAL == 0 or _heartbeat_count % HEARTBEAT_LOG_INTERVAL == 0:
            log.info("♥ Heartbeat #%d", _heartbeat_count)
        return

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning("Part não-JSON (name=%s): %s", name, body[:200])
        publish(f"{MQTT_TOPIC_BASE}/any", body, retain=MQTT_RETAIN_ANY)
        return

    event_type = data.get("eventType", name or "unknown")
    inner = data.get(event_type, data)

    major = str(inner.get("majorEventType", "unknown"))
    minor = str(inner.get("subEventType", inner.get("minorEventType", "unknown")))
    user_name = inner.get("name", "")
    employee = inner.get("employeeNoString", "")

    topic_specific = f"{MQTT_TOPIC_BASE}/{major}/{minor}"
    topic_any      = f"{MQTT_TOPIC_BASE}/any"

    payload = json.dumps(data, ensure_ascii=False)

    publish(topic_specific, payload)
    publish(topic_any, payload, retain=MQTT_RETAIN_ANY)

    log.info(
        "Evento: type=%s major=%s minor=%s door=%s user=%s(%s)",
        event_type, major, minor,
        inner.get("doorNo", "-"),
        user_name, employee,
    )


def run_alertstream():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    while True:
        session = requests.Session()
        session.verify = False
        session.auth = HTTPDigestAuth(HIK_USER, HIK_PASS)
        log.info("Conectando ao alertStream: %s", ALERT_STREAM_URL)
        try:
            with session.get(
                ALERT_STREAM_URL,
                stream=True,
                timeout=(10, None),
                headers={"Accept": "multipart/form-data"},
            ) as response:
                response.raise_for_status()
                log.info("alertStream aberto (HTTP %s)", response.status_code)
                for name, content_type, body in stream_parts(response):
                    process_event(name, content_type, body)

        except requests.exceptions.ConnectionError as e:
            log.error("Conexão perdida: %s — reconectando em %ds", e, RECONNECT_DELAY)
        except requests.exceptions.Timeout:
            log.warning("Timeout — reconectando em %ds", RECONNECT_DELAY)
        except requests.exceptions.HTTPError as e:
            log.error("HTTP erro: %s — reconectando em %ds", e, RECONNECT_DELAY)
        except Exception as e:
            log.exception("Erro inesperado: %s — reconectando em %ds", e, RECONNECT_DELAY)

        time.sleep(RECONNECT_DELAY)


def main():
    global _mqtt_client
    log.info("Iniciando hikvision-alertstream bridge")
    _mqtt_client = build_mqtt_client()
    if not _mqtt_connected.wait(timeout=15):
        log.error("Não foi possível conectar ao MQTT — abortando")
        raise SystemExit(1)
    run_alertstream()


if __name__ == "__main__":
    main()

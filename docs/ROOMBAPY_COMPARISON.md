# Vergleich mit `roombapy` (Referenz, PyPI 1.9.1)

Quelle: `pip download roombapy` → `roombapy-1.9.1.tar.gz`, echter
Quellcode, kein Auszug/keine Rekonstruktion.

## 1. Modul-Zuordnung

| roombapy | roombapy-prime | Beziehung |
|---|---|---|
| `remote_client.py` | `mqtt_client.py` | Gleicher Zweck (MQTT-Transport), **strukturell inkompatibel** (siehe 2) |
| `roomba.py` | `prime_robot.py` | Gleicher Zweck (öffentliche Klasse), **anderes Protokoll-Paradigma** (siehe 3) |
| `roomba_factory.py` | `prime_factory.py` | Gleiches Muster übernehmbar (siehe 4) |
| `roomba_info.py` | `models.py` | Gleicher Zweck, `mashumaro`-Pattern übernehmbar (siehe 4) |
| `discovery.py` | — kein Äquivalent | mDNS-Broadcast-Discovery im lokalen Netz — für Prime irrelevant, es gibt kein "lokales Netz" |
| `getpassword.py` | — kein Äquivalent | BLE-basierte Passwort-Abholung vom Gerät selbst — Prime nutzt Account-Login, kein Geräte-BLE |
| `const.py` | — noch nicht angelegt | Fehlercode-Tabellen, siehe 4 |

## 2. TLS/Auth-Inkompatibilität — jetzt mit echtem Code belegt

`remote_client.py`, `generate_tls_context()`:
```python
@cache
def generate_tls_context() -> ssl.SSLContext:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    ssl_context.verify_mode = ssl.CERT_NONE
    ...
```
Und in `_get_mqtt_client()`:
```python
mqtt_client.tls_set_context(ssl_context)
mqtt_client.tls_insecure_set(True)
mqtt_client.username_pw_set(username=self.blid, password=self.password)
```
Bestätigt exakt das, was im Handoff als Grund für die separate Bibliothek
stand: `ssl.CERT_NONE` global gecacht (`@cache`), `tls_insecure_set(True)`,
Login über `blid`/`password` als MQTT-Username/Passwort — strukturell
unvereinbar mit dem Custom-Authorizer-Header-Mechanismus, den
`mqtt_client.py` bereits nutzt (echte Zertifikatsprüfung über `certifi`,
drei Auth-Header statt Username/Passwort).

## 3. Tieferer Unterschied als nur Sicherheit: das Protokoll-Paradigma selbst

`roomba.py` ist um ein **kontinuierliches Push-Modell** herum gebaut:
- Abonniert Wildcard-Topic `#` (Firehose, alles was der Roboter sendet)
- `dict_merge()` flacht jede eingehende Nachricht in einen wachsenden
  `master_state`-Dict
- `decode_topics()` reduziert verschachtelte Keys zu flachen,
  unterstrichverketteten Namen (`state_reported_` wird sogar explizit
  weggeschnitten)
- Eine eigene Phasen-Zustandsmaschine (`update_state_machine()`,
  `cleanMissionStatus_phase`-Übergänge) rekonstruiert den Missionsstatus
  aus der Nachrichtenfolge

Das ist grundsätzlich anders als das, was für Prime bereits bestätigt ist:
**Anfrage/Antwort** (Shadow-`get`/`update`, siehe `mqtt_client.py`) plus
**sitzungsgebundene Topic-Subscription** (die neu gefundene
`/v1/p2maps/livemap`-Mechanik, siehe `FINDINGS_2026-07-11.md`) — kein
Wildcard, kein Firehose, kein Bedarf an einer eigenen Merge-/
Flatten-Logik, weil jede Antwort bereits vollständig strukturiert kommt.

**Konsequenz:** Die Trennung war nicht nur wegen TLS/Auth richtig,
sondern weil `prime_robot.py` ohnehin keinen `dict_merge`/
`decode_topics`-Mechanismus bräuchte, selbst wenn `remote_client.py`
technisch wiederverwendbar gewesen wäre. Der State-Aufbau-Ansatz selbst
passt nicht zum Anfrage/Antwort-Charakter des Cloud-Wegs.

## 4. Muster, die sich lohnen zu übernehmen

- **`mashumaro`** (`DataClassORJSONMixin`, `field_options(alias=...)`) für
  typisierte Dataclasses mit JSON-Feld-Aliasing — z.B. in `roomba_info.py`:
  ```python
  firmware: str = field(metadata=field_options(alias="sw"))
  ```
  Lohnt sich jetzt besonders für `models.py`, da aus der heutigen
  Analyse schon recht viel Rohschema bekannt ist (RoomType/FurnitureType-
  Enums, `connection_tokens`-Feldnamen, p2maps-Kommando-Payloads) — sauberer
  als die aktuelle rohe Dict-Indizierung in `auth.py`.
- **Factory-Namenskonvention**: `RoombaFactory.create_roomba(address, blid,
  password, ...)` als statische Methode — für `prime_factory.py` analog:
  `PrimeFactory.create_prime_robot(username, password, blid, ...)`.
- **Lesbare Fehlercode-Tabelle** (`MQTT_ERROR_MESSAGES: dict[int, str]`) —
  `mqtt_client.py`s `_on_connect` speichert aktuell nur `str(reason_code)`.
  Eine äquivalente Tabelle für AWS-IoT-/Shadow-Reject-Gründe wäre
  sinnvoll, sobald mehr echte (nicht nur synthetische) Reject-Payloads
  vorliegen.
- **Callback-Registrierung-Namenskonvention** (`register_on_message_callback`,
  `register_on_disconnect_callback`) — für Wiedererkennbarkeit, falls
  `prime_robot.py` später von denselben Entwicklern genutzt wird, die
  `roombapy`/`ha_roomba_plus` schon kennen.

## 5. Eine Sache, wo roombapy-prime schon voraus ist

`roombapy`s `pyproject.toml` pinnt `paho-mqtt >=1.6.1,<3.0.0` — nutzt
also noch die alte v1-Callback-API (kein `callback_api_version`-Parameter).
`mqtt_client.py` nutzt bereits explizit `CallbackAPIVersion.VERSION2`
(paho-mqtt 2.x). Das ist eine bewusste, keine zufällige Voraus-Entscheidung
— nur zur Kenntnisnahme, kein Handlungsbedarf.

# Quervergleich mit ha_roomba_plus v3.4.1 (`cloud_api.py`)

Dritte, unabhängige Bestätigungsquelle neben Live-Tests (Classic-
Protokoll-Fixtures) und APK-Analyse (Kotlin-Quellcode): der bereits
produktiv laufende Integration-Code selbst. `cloud_api.py` implementiert
denselben Login-Fluss, den `auth.py` schon aus validierten Testskripten
übernommen hatte — aber geht für die Classic-REST-Endpunkte (SMART-Tier
`/v1/{blid}/pmaps` etc.) einen Schritt weiter, den `rest_client.py`
bisher nicht kannte.

## Was übernommen wurde

### 1. AWS SigV4-Signierung (`aws_sigv4.py`, neu)

`cloud_api.py`s `_AWSSignatureV4`-Klasse 1:1 portiert (Umbenennung,
keine Logikänderung an der Kernsignierung). Byte-für-Byte gegen das
Original verifiziert (eingefrorene Uhrzeit, identische Eingaben,
identische Ausgabe) — siehe `test_aws_sigv4.py`s Regressionstest.

**Meine Erweiterung, nicht Teil des Originals:** Das Original signiert
ausschließlich GET ohne Body. p2maps braucht auch POST mit JSON-Body
(`edit_map`, `set_map_name`, etc.) — der `body`-Parameter und die
daraus berechnete `payload_hash` sind meine Erweiterung. Der
SigV4-Algorithmus selbst verlangt das exakt so, aber nie gegen einen
echten POST-Aufruf mit Body getestet, weder Classic noch Prime/V4.

### 2. `httpBaseAuth` statt `httpBase` (echter Bug-Fix)

`cloud_api.py` nutzt `deployment['httpBase']` **ausschließlich** für
`/v2/login`, und `deployment['httpBaseAuth']` für **alle** authentifizierten
Datenendpunkte (`/v1/{blid}/pmaps`, `/v1/{blid}/missionhistory`,
`/v1/user/favorites`, `/v1/user/automations`) — konsistent über 6
Aufrufstellen. `rest_client.py` nutzte vorher `http_base` für p2maps,
was nach diesem Muster schlicht falsch war. Jetzt korrigiert:
`LoginResult.http_base_auth`, mit Gate-Validierung in `login()`
(fehlt das Feld, schlägt der Login sofort fehl, nicht erst ein
späterer REST-Aufruf mit einer verwirrenden URL).

### 3. `credentials`-Block (`CloudCredentials`, neu in `auth.py`)

Der `credentials`-Block der Login-Antwort (`AccessKeyId`, `SecretKey`,
`SessionToken`, `CognitoId`, `Expiration`) war in `auth.py` bisher
komplett ignoriert — obwohl er in beiden echten Fixtures vorhanden ist.
Das ist genau das, was SigV4 zum Signieren braucht. Jetzt: eigene
`CloudCredentials`-Dataclass, Validierung am Login-Gate (Lektion aus
`cloud_api.py`s eigenem "v3.3.0 REVIEW-REMAINDER"-Kommentar: fehlende
Felder sollen beim Login laut fehlschlagen, nicht spät mit einem
`KeyError`).

### 4. Reaktiver 403-Retry (`rest_client.py`)

`cloud_api.py`s `_aws_get()`: bei HTTP 403 einmalig neu einloggen,
Aufruf wiederholen (`_retry`-Flag verhindert Endlosschleife). 1:1
übernommen, ergänzt den bereits vorhandenen **proaktiven** Refresh auf
der MQTT-Seite (`PrimeRobot._refresh_loop()`, zeitbasiert) um einen
**reaktiven** Mechanismus auf der REST-Seite.

### 5. Ratenlimit-Fehlermeldung (`auth.py`)

`cloud_api.py` erkennt "mqtt slot" in der Fehlermeldung und gibt einen
klareren Hinweis ("Cloud auth rate-limited. Close the iRobot app and
try again."). Ein echter, bekannter Fehlermodus — 1:1 übernommen.

### 6. Logging-Konvention

`logging.getLogger(__name__)` durchgängig ergänzt (`auth.py`,
`mqtt_client.py`, `rest_client.py`, `prime_robot.py`) — entspricht
sowohl `roombapy`s als auch `ha_roomba_plus`s eigener Konvention.

## Was NICHT übernommen wurde (und warum)

- Retry/Backoff-Logik für die Discovery-/Gigya-Anfragen selbst — in
  `cloud_coordinator.py` nicht vorhanden (verlässt sich auf HAs eigenen
  `ConfigEntryNotReady`-Mechanismus, der für eine eigenständige
  Bibliothek nicht existiert). Kein Vorbild zum Übernehmen.
- Alles `hass.storage`-/Store-bezogene (STORE_VERSION-Trennung etc.) —
  HA-spezifisch, keine Entsprechung in einer eigenständigen Bibliothek.
- Fehlercode-Tabellen (`ROOMBA_ERROR_MESSAGES` etc.) — Classic-Protokoll-
  spezifische Missionsfehler, keine Entsprechung für p2maps/Shadow-
  Ablehnungsgründe gefunden.

## Was weiterhin offen bleibt

- Ob p2maps überhaupt SigV4-Signierung braucht, ist eine Analogie-
  Annahme aus anderen `/v1/`-Endpunkten derselben Cloud-API-Familie,
  keine für p2maps selbst bestätigte Tatsache.
- Kein einziger dieser REST-Aufrufe wurde bisher tatsächlich ausgeführt
  — weder gegen Classic- noch gegen Prime/V4-Endpunkte.

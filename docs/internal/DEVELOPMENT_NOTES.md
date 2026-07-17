# roombapy-prime — Entwicklungsnotizen (Deutsch, Arbeitssprache des Betreuers)

## Update (dritte Sitzung, 11. Juli): native Disassemblierung + Missionssteuerung

Kurzfassung, Details in `PRIME_APP_GAP_ANALYSIS_2026-07-11.md`:

- **Missionssteuerung (CLEAN/START/STOP/PAUSE/DOCK/etc.) implementiert**
  (`models.py::RoutineCommand`/`MissionCommandType`,
  `prime_robot.py::send_mission_command()`) -- vorher als "strukturell
  harte native Grenze" eingestuft, das war nur zur Haelfte richtig.
  Transport bestaetigt via aarch64-objdump-Disassemblierung von
  `liblegacyCore.so` (woertlicher Format-String
  `$aws/things/%s/shadow/update`), Payload-Form bestaetigt aus
  `@Serializable`-Kotlin-Quellcode (`CommandWrapper`/`RoutineCommand`,
  echte `@SerialName`-Annotationen). Nie GEMEINSAM live getestet.
- **Nebenlaeufigkeitsschutz** (`threading.Lock`) zwischen
  `replace_token()` und `get_shadow()`/`update_shadow()` -- vorher
  dokumentierte Luecke jetzt geschlossen, mit echtem Multi-Thread-Test
  verifiziert.
- **Backpressure**: verworfene Fehler jetzt als ERROR statt WARNING
  geloggt.
- **Housekeeping**: `py.typed`, GitHub-Actions-CI (fand einen echten
  ungenutzten Import via ruff), englisches nutzerseitiges README
  (dieses Dokument hier ist jetzt die interne, deutsche Notizen-Datei).
- **Ader-Update-Entwurf** geschrieben (`../archive/ADER_UPDATE_DRAFT_2026-07-11.md`).
- Testzahl: 102 (von 95).

---

Separate Bibliothek für iRobot "Prime"/V4-Cloud-Anbindung. Nicht auf
`roombapy` aufgebaut (siehe Begründung in `roombapy_prime/__init__.py`
und `ROOMBAPY_COMPARISON.md`).

**Status: Draft. Läuft, ist getestet, ist NICHT gegen ein echtes
V4-Konto verifiziert.**

11. Juli 2026: Auf ausdrücklichen Wunsch nicht länger auf Aders Antwort
oder echte V4-Felddaten gewartet — alle Module sind jetzt als
lauffähiger Entwurf implementiert, durchgehend auf Basis dessen, was
bereits bestätigt ist (Classic-Protokoll-Mechanik für auth.py/
mqtt_client.py; Java-Quellcode-Analyse für rest_client.py/models.py).
Jede Unsicherheit ist im jeweiligen Modul-Docstring benannt, nicht
versteckt. Sobald echte V4-Daten vorliegen, sind Korrekturen erwartet,
keine Überraschung.

Siehe `../archive/FINDINGS_2026-07-11.md` (Rohbefunde aus der APK-Analyse),
`ROOMBAPY_COMPARISON.md` (Vergleich mit dem echten
`roombapy`-Quellcode), `../archive/HA_ROOMBA_PLUS_CROSSREF.md` (Vergleich
mit `ha_roomba_plus`s produktivem `cloud_api.py` — AWS-SigV4,
httpBaseAuth, 403-Retry) und `PRIME_APP_GAP_ANALYSIS_2026-07-11.md`
(detaillierter Soll/Ist-Abgleich gegen die Prime-App selbst — was
implementiert ist, was fehlt, was davon eine echte native Grenze ist).

**Wichtigste offene Lücke laut Gap-Analyse:** Missionssteuerung
(Start/Stop/Pause/Dock) ist NICHT implementiert. Vollständiges
Kommando-Vokabular bekannt, aber die tatsächliche Übertragung läuft
über natives `CommandTierAgentImpl::postCommand()` — für Java/Kotlin-
Analyse unsichtbar, dieselbe native Grenze wie beim Login und der
Positionsübertragung.

## Tests

```
pip install -e .
pytest roombapy_prime/tests/
```
95 Tests, alle grün (Stand 11. Juli 2026, mehrfach wiederholt zur
Flakiness-Kontrolle der Thread-Bruecken-Tests). Abdeckung: `ConnectionToken`-
und `CloudCredentials`-Parsing (echte Fixtures), Shadow-Get/-Update (echte
Fixtures, beide Tiers), p2maps-Kommando-Envelopes, Lese-Modelle und
Geometrie-Serialisierung (synthetisch, aber gegen die Java-Quellstruktur
geprüft), Livemap-Nachrichtenparsing (`cur_path`-Trajektorien-Logik,
synthetisch aber strukturgetreu), AWS-SigV4-Signierung (Regressionspin
gegen die verifizierte Original-Ausgabe), REST-Client (URL/Body/
Fehlerpfade/403-Retry/`get_active_map_versions()`, eigener Fake statt
`aioresponses` wegen einer aiohttp-3.14.1-Inkompatibilität), Factory-/
Robot-Verdrahtung (Smoke-Tests mit Mocks), kontinuierliche Dispatch-
Schleifen (`watch_state()`/`watch_live_map()` mit festem Livemap-Topic
+ Hintergrund-Keep-Alive, inkl. Mehrfach-Watcher-Referenzzählung und
Fehlerpropagation bei unbekannten Nachrichtenformen), proaktiver
Token-Refresh (`replace_token()`, `_refresh_loop()`, `token_for_blid()`),
Backpressure
(`_put_with_backpressure()`, Drop-Oldest + Logging).

Kein Test ersetzt einen echten Lauf gegen einen echten Server — das
gilt für Classic-Fixtures genauso wie für alles p2maps-/V4-Bezogene.

## Modul-Vertrauensstand (Stand 11. Juli 2026)

| Modul | Vertrauensstand | Begründung |
|---|---|---|
| `auth.py` | Hoch (Mechanismus) | Login-Fluss funktionierender, getesteter Code (Classic); Prime nutzt laut `liblegacyCore.so` denselben nativen Kern — aber nie gegen echtes V4-Konto getestet. Seit heute: `CloudCredentials`-Parsing (echte Fixtures), Gate-Validierung, Rate-Limit-Fehlermeldung — alle drei aus `ha_roomba_plus`s produktivem `cloud_api.py` übernommen |
| `mqtt_client.py` | Hoch (Verbindungsschicht) | WebSocket+Custom-Authorizer live bestätigt (Classic-Geräte via Cloud-Shadow-Test), unverändert seit Review |
| `aws_sigv4.py` (neu) | Hoch (Algorithmus), unverifiziert (p2maps-Anwendung) | Byte-für-Byte identisch zu `ha_roomba_plus`s produktivem Signierer verifiziert (siehe `../archive/HA_ROOMBA_PLUS_CROSSREF.md`) — POST-mit-Body ist aber meine eigene, ungetestete Erweiterung |
| `rest_client.py` (p2maps) | Mittel-Hoch (Struktur), unverifiziert (Praxis) | Vollständiges REST-Kommandovokabular auf Java-Ebene bestätigt (10 Kommandotypen, exakte JSON-Form inkl. GeoJSON-Geometrie). Seit heute: SigV4-Signierung, `httpBaseAuth` (echter Bug-Fix, vorher `httpBase` — siehe CROSSREF-Doku), reaktiver 403-Retry — alle drei aus `cloud_api.py` übernommen. Weiterhin: kein einziger Aufruf je gegen einen echten Server ausgeführt |
| `models.py` | Mittel-Hoch (Struktur), unverifiziert (Praxis) | RoomType/FurnitureType-Werte und Geometrie-Nesting direkt aus Java-Quellcode, cur_path-Trajektorien-Logik 1:1 aus dem Original portiert — Kommando-Envelopes selbst nie live bestätigt |
| `prime_robot.py` / `prime_factory.py` | Draft (Verdrahtung + Dispatch + Refresh + Backpressure) | Verdrahtung der obigen Bausteine PLUS kontinuierliche Dispatch-Schleifen (`watch_state()`, `watch_live_map()`) PLUS proaktiver Token-Refresh (`auto_refresh=True`) PLUS begrenzte Puffer mit Drop-Oldest-Politik (`queue_maxsize`, Default 100) |

**AWS-SigV4/httpBaseAuth/403-Retry/CloudCredentials/Rate-Limit-Meldung:**
alle fünf aus `ha_roomba_plus`s bereits produktiv laufender
`cloud_api.py` übernommen — eine dritte, unabhängige Bestätigungsquelle
neben Live-Tests und APK-Analyse. Details, was übernommen wurde und
warum, was bewusst nicht übernommen wurde, und was weiterhin offen
bleibt: siehe `../archive/HA_ROOMBA_PLUS_CROSSREF.md`.

**Backpressure (`watch_state()`/`watch_live_map()`):** begrenzte
`asyncio.Queue` (Default 100, konfigurierbar über `queue_maxsize`),
Drop-Oldest bei vollem Puffer (Aktualität vor Vollständigkeit), jeder
Drop wird als WARNING geloggt. Bekannte Einschränkung: Fehler
durchlaufen dieselbe Queue wie normale Nachrichten in `watch_live_map()`
und sind damit theoretisch ebenfalls von Drop-Oldest betroffen — kein
Sonderfall dafür eingebaut.

**Kontinuierliche Dispatch-Schleifen (`watch_state()`, `watch_live_map()`):**
additiv in `mqtt_client.py` ergaenzt (`subscribe()`/`unsubscribe()`,
referenzgezaehlt fuer mehrere gleichzeitige Watcher auf demselben
Topic), bestehende `get_shadow()`/`update_shadow()`-Logik unangetastet.
Kein Lock noetig — jeder Watcher bekommt seine eigene `asyncio.Queue`
— aber das referenzgezaehlte Unsubscribe war noetig, damit zwei
Watcher auf demselben Topic sich nicht gegenseitig abwuergen.

**Proaktiver Token-Refresh:** Es gibt keinen Refresh-Endpunkt (siehe
`auth.py`) — "Refresh" heisst hier: `PrimeMqttClient.replace_token()`
loggt NICHT selbst neu ein, sondern nimmt einen bereits frischen Token
entgegen, trennt, verbindet neu, stellt alle laufenden `subscribe()`-
Topics automatisch wieder her. Das eigentliche Neu-Einloggen passiert
in `PrimeRobot._refresh_loop()`, geplant 5 Minuten vor Ablauf (`
REFRESH_MARGIN_SECONDS`, willkuerlich gewaehlt). Opt-in ueber
`PrimeFactory.create_prime_robot(..., auto_refresh=True)` — dieser
selbe Callback deckt seit heute auch den reaktiven REST-403-Retry ab.
Default bleibt `False`, damit bestehende Aufrufer keine Ueberraschung
erleben. **Bewusster Tradeoff:** `auto_refresh=True` haelt
Zugangsdaten (username/password, im relogin-Callback-Closure) fuer die
gesamte Lebensdauer der `PrimeRobot`-Instanz im Speicher, nicht nur
fuer den einmaligen Login-Moment. Nebenbei behoben: `token_for_blid()`
ersetzt das bisherige blinde `connection_tokens[0]` — fuer Multi-
Roboter-Konten waere sonst nicht sichergestellt, dass der gewaehlte
Token tatsaechlich den gewaehlten Roboter abdeckt (nie an echten Daten
geprueft, da beide Fixtures nur je einen Roboter/Token haben).

Weiterhin nicht Teil dieses Drafts: Nebenlaeufigkeitsschutz zwischen
`replace_token()` und einem gleichzeitig laufenden `get_shadow()`/
`update_shadow()`-Aufruf.

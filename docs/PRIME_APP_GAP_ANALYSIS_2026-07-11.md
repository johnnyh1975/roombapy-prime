# Detailaudit: Prime-App-Pakete/Analyse vs. roombapy-prime — Stand 11. Juli 2026

## AKTUELLER STAND (nach neunter Sitzung) — diese Zusammenfassung zuerst lesen

Der Rest des Dokuments ist chronologisch (neueste Sitzung oben), gewachsen ueber 9 Sitzungen.
Diese Zusammenfassung fasst zusammen, was JETZT gilt, ohne die Historie durchsuchen zu muessen.

**Vollstaendig implementiert und bytecode-/quellcode-bestaetigt:**
Auth-Kette, MQTT-Shadow-Client, alle 11 P2Map-Lese-Endpunkte + Lese-Modelle, V1-Editier-Vokabular
(9 Kommandos, der tatsaechlich aktive Pfad), Missionssteuerung (`RoutineCommand` inkl. `CommandParams`
mit 37 Feldern, `Region`/`CommandPolygon`/`PadWetnessParam`), Favoriten (alle 5 Endpunkte inkl. HTTP-
Methoden), Zeitplaene (`ScheduleOptions`/`HouseholdSchedule`, alle 4 HTTP-Methoden bestaetigt), DND-
Einstellungen, Reinigungsprofile, Standard-Routinen, Missionshistorie (Anfrage UND Antwort-Top-Level),
tar.gz-Kartenbuendel (Download+Entpacken), Haushaltslisting (trotz App-seitigem Totcode-Status).

**Echte, aber NICHT weiter aufloesbare Luecken** (brauchen ein echtes Geraet oder sind strukturell
unerreichbar durch Analyse):
- Exaktes Envelope-Format der V1-Editier-Kommandos (Diskriminator-Schluessel unbekannt, custom
  Serializer nicht dekompilierbar)
- `irbt_topic_prefix`s exakter JSON-Feldname (Konzept bestaetigt, String nicht gefunden)
- p2maps-Auth-Mechanismus: SigV4-Annahme bleibt Analogie zu Classic, Primes eigener Code delegiert
  nachweislich an native `accountService.sendRequest()` -- prinzipiell nie aus Kotlin/Java-Code
  bestaetigbar, nur durch echten Traffic-Capture
- Dateibenennung innerhalb des tar.gz-Kartenbuendels
- `HouseholdSettingOptions`-Struktur, 16 von 20 `MissionTimelineEvent`-Unterereignistypen (nur
  PlanEvent/PolygonEvent/TravelEvent/TraversalEvent im Detail typisiert -- Aufwand/Nutzen-Grenze)
- Teaming/Mehrgeraete-Koordination -- nicht untersucht, braucht mehrere Testgeraete im Haushalt

**Bekannte False-Positives aus frueheren Sitzungen, seither aufgeloest:**
- "Furniture-Editierbefehl fehlen 2 Felder" (B2 unten) -- war ein Vergleichsfehler
  Lese-Modell-vs-Schreib-Modell; das reale `EditMapV2Request.Furniture` hat nur 4 Felder
  (geometry/id/type/userModified), exakt was die Bibliothek bereits hatte
- "V1 ist fuer aeltere Firmware" -- falsch, V1 ist schlicht der einzige aktive Pfad (alle
  Firmware-Generationen), V2 ist komplett totes Gewebe
- Alter C1-Abschnitt unten ("Missionssteuerung nicht baubar") -- durch spaetere Sitzungen ueberholt,
  Missionssteuerung ist implementiert

**Testabdeckung:** 139/139 Tests gruen, ruff sauber.

## Nachtrag (elfte Sitzung, selber Tag): Korrektur + Live-Diagnoseskript

**Korrektur:** Die in der neunten Sitzung als Indiz vorgeschlagene Deutung von
`RoutineCommand.ordered` ("impliziert Sequenzierung mehrerer commandDefs-Eintraege") wurde vom
Parallelchat zurecht widerlegt: `ha_roomba_plus` (jahrelang produktiv gegen echte Classic-Geraete)
nutzt `ordered` als reine INTRA-Command-Eigenschaft neben `regions` im selben Kommando-Objekt --
ob die Regionen INNERHALB dieses einen Kommandos in Reihenfolge angefahren werden oder der Roboter
optimieren darf. Hat nichts mit der Anzahl separat verschickter Kommandos zu tun. Docstring in
`models.py` entsprechend korrigiert. Die urspruengliche Frage (iteriert die App ueber mehrere
`commandDefs`-Eintraege?) bleibt unentschieden.

**Neu: `roombapy_prime/diagnostics.py`** -- ein Live-Validierungsskript, direkt aus der wiederholt
genannten Kernschwaeche der Bibliothek entstanden (nichts wurde je gegen einen echten Account
getestet). Rein lesend per Standard (Login, REST-Reads, Shadow-Zustand, Kartenbuendel-Download);
`--allow-writes` schaltet einen reversiblen Favoriten-Anlegen/Pruefen/Loeschen-Rundlauf frei, der
live bestaetigen wuerde, ob die drei bisher nur per Bytecode bestaetigten HTTP-Methoden
(create/update/delete Favorite) tatsaechlich vom Server akzeptiert werden. Missionsbefehle und
Kartenbearbeitung werden bewusst NIE automatisch ausgefuehrt (Risiko einer echten Aktion am
physischen Geraet). CLI-Einstiegspunkt `roombapy-prime-validate` in pyproject.toml registriert.
Rauchtest mit ungueltigen Zugangsdaten bestaetigt sauberes Fehlschlagen (kein Absturz, klarer
Bericht, Exit-Code 1).

Nebenbei eine kleine Luecke geschlossen: `PrimeRobot.get_active_map_versions()` fehlte bisher als
Wrapper, obwohl die rest_client.py-Version schon lange existierte.

145/145 Tests gruen (6 neue fuer die testbaren Teile von diagnostics.py -- Report-Klasse und
Hilfsfunktionen; das eigentliche Live-Skript kann seiner Natur nach nicht ohne echten Account
getestet werden), ruff sauber.

**Nachtrag zum Nachtrag (zwoelfte Sitzung):** Auf Wunsch ergaenzt -- das Skript druckt am Ende
jedes Laufs jetzt einen vorausgefuellten GitHub-"Neues Issue"-Link (Titel + kompletter Bericht als
Body, URL-kodiert), damit jemand mit einem echten Account die Ergebnisse mit einem Klick teilen
kann. Davor durchlaeuft der Bericht eine Redaktionsstufe (`Report.redact()`), die jedes woertliche
Auftreten von Benutzername/Passwort in Fehlertexten durch "[REDACTED]" ersetzt -- Verteidigung in
der Tiefe, auch wenn Zugangsdaten im Normalfall nirgends in Berichtseintraege geschrieben werden.
`ISSUE_TRACKER_REPO` ist ein Platzhalter-Konstante (`"OWNER/roombapy-prime"`), die auf den echten
Repo-Pfad umzustellen ist, sobald das Repo existiert -- der Link funktioniert unabhaengig davon
(reine URL-Konstruktion, kein API-Aufruf), zeigt bis dahin aber ins Leere; das Skript weist selbst
darauf hin. `--no-issue-link`/`--open-browser` als zusaetzliche Flags. 150/150 Tests gruen.

## Nachtrag (zwoelfte Sitzung, selber Tag): Release-Vorbereitung -- LICENSE + CI

Auf die Frage "sind wir release-faehig fuer v0.1 Beta" ehrliche Antwort gegeben: NEIN, "Beta" waere
irrefuehrend -- kein einziger erfolgreicher Lauf gegen einen echten Account existiert, und die
Kernfrage (funktioniert Login/Missionssteuerung ueberhaupt gegen den echten Server?) ist komplett
offen, nicht nur "hat noch Ecken und Kanten". Zwei konkrete, jetzt behobene Blocker unabhaengig
davon aber erledigt:

- **`LICENSE`** (MIT, konsistent mit `roombapy`s eigener Lizenzwahl) angelegt.
  `pyproject.toml` auf PEP-639-Stil (`license = "MIT"` + `license-files`) umgestellt, inkl.
  Klassifikatoren -- bewusst `"Development Status :: 2 - Pre-Alpha"`, nicht Beta, passend zur
  obigen Einschaetzung. Build lokal verifiziert: sdist+wheel bauen sauber, Lizenz landet korrekt in
  `dist-info/licenses/LICENSE`, Metadaten zeigen `License-Expression: MIT`.
- **CI verschaerft**: Der Lint-Job hatte bisher `continue-on-error: true` (aus der Zeit, als ruff
  noch nicht durchgehend sauber war) -- entfernt, da ruff seit mehreren Sitzungen ausnahmslos
  clean ist. Neuer `build`-Job: baut sdist+wheel, installiert das Wheel in einer frischen,
  isolierten venv, importiert das Paket -- validiert, dass die Bibliothek tatsaechlich
  installierbar ist, nicht nur dass die Tests im Repo laufen. Alle drei Schritte lokal
  durchprobiert, bevor sie ins CI-File kamen.

README's Lizenz-Abschnitt von "TBD" auf "MIT" aktualisiert.

150/150 Tests weiterhin gruen, ruff sauber, Build+Install-Verifikation lokal bestaetigt.

## Nachtrag (vierzehnte Sitzung, selber Tag): Transportmechanismus fuer Missionsbefehle -- zwei Ketten
## untersucht, keine bestaetigt (Korrektur nach Nachfrage)

Auf Bitte des Parallelchats (MVP-Frage: MQTT, REST, oder Shadow fuer den Missionsstart?) wurde
`liblegacyCore.so` und `libcorebase.so` vollstaendig mit Ghidra analysiert. Eine vielversprechende
Kette wurde gefunden und zunaechst als bestaetigt behandelt -- auf Nachfrage genauer geprueft, dabei
ein echter Widerspruch entdeckt. Beide Ketten hier dokumentiert, keine davon tragfaehig:

**Kette A (klassischer Shadow):** urspruenglicher Stand, aus einem generischen String-Fund
(`"$aws/things/%s/shadow/update"`) gefolgert. Dieser String ist generisch (nicht Missions-
spezifisch), die Wahl "klassisch statt benannt" war selbst nie eigenstaendig belegt.

**Kette B (NAMED "rw-settings"-Shadow, untersucht und wieder verworfen):**
```
CloudCapableMissionUIService::sendCommandJson(json)  [AWS-Zweig]
  -> vermutet: PMIAssetService::postCommand(type, json)   [Vtable-Slot 0x38]
    -> getMqttTopic(type)
      -> ThingShadowConstants::supportedNamedShadowTopics()[5] == "rw-settings"
```
Bei genauer Pruefung (Argumentzahl-Abgleich, nicht nur thematische Plausibilitaet) zeigte sich:
`sendCommandJson()`s tatsaechliche Aufrufstelle uebergibt nur EIN String-Argument, waehrend
`PMIAssetServiceImpl::postCommand()` bestaetigt ZWEI braucht (`mov x21, x2` bei dessen eigenem
Funktionseintritt, aus der urspruenglichen Disassemblierung). Die Verbindung sendCommandJson ->
postCommand war also nur thematisch plausibel (beide "nehmen einen JSON-String"), nicht durch
Argumentabgleich bestaetigt -- vermutlich falsch. Zusaetzlich: die VOLLSTAENDIGE
`mapCommandsToNamedTopics()`-Tabelle (die postCommand fuettert, alle 14 Eintraege durchgesehen:
`SetBinPauseCommand`, `SetCarpetBoostCommand`, `SetEdgeCleanCommand`, `SetSuctionLevelCommand`,
`SetRobotPadWetnessCommand`, `SetAssetLanguageCommand`, `SetEchoCommand`, `AssetScheduleCommand`,
`AssetNameCommand`, `SetAssetPreferencesCommand`, `SetMapUploadAllowedCommand`,
`SetMultiPassCommand`, `SetRobotPadPlateWetnessCommand`, `SetRobotRankOverlapCommand`) deckt
ausschliesslich EINSTELLUNGS-Kommandos ab -- kein einziges Missions-Start/Clean/Dock-Kommando war
darunter, selbst wenn die Verbindung gestimmt haette.

**Konsequenz:** `send_mission_command()` wurde kurzzeitig auf `"rw-settings"` umgestellt, dann nach
dieser Pruefung wieder auf den klassischen Shadow zurueckgesetzt. Der Docstring dokumentiert jetzt
ehrlich beide untersuchten, keine bestaetigte Kette. Dies bleibt der genuin unsicherste Teil der
gesamten Bibliothek -- eine definitive Antwort braucht entweder einen vollstaendigeren nativen
Trace (den tatsaechlichen Aufrufer von `sendCommandJson`s korrektem Gegenstueck finden) oder einen
echten Live-Test.

**Methodische Lehre:** Eine vtable-Slot-Aufloesung ueber RTTI-Typinfo-Lesen (wie hier fuer
`AssemblerImpl`s Mehrfachvererbung von `Assembler`+`CoreInjector` gezeigt) ist ein maechtiges
Werkzeug, ersetzt aber nicht den Argumentzahl-/Signatur-Abgleich an der tatsaechlichen Aufrufstelle
-- thematische Plausibilitaet (”beide nehmen einen String") reicht nicht als Bestaetigung.

171/171 Tests gruen, ruff sauber.

## Nachtrag (achtzehnte Sitzung): alle 20 MissionTimelineEvent-Unterereignistypen typisiert

Die in der neunten Sitzung gezogene Aufwandsgrenze wurde aufgehoben. Beim Umsetzen zeigte sich:
die 4 Typen, die als "bereits im Detail bytecode-inspiziert" dokumentiert waren (PlanEvent,
PolygonEvent, TravelEvent, TraversalEvent), existierten tatsaechlich nur als Analyse-Notiz im
Docstring -- nie als echter Code. Alle 20 wurden jetzt neu implementiert:

- **15 Klassen sauber per jadx dekompiliert**: CommandEvent, DiscoveryEvent, ErrorEvent, EvacEvent,
  LiveViewEvent, PadDryEvent, PadWashEvent, PanoramaEvent, RefillEvent, RoomEvent, SubRoomEvent,
  TentativeLocationEvent, WaypointEvent, WetOutEvent, ZoneEvent
- **4 weitere per androguard** (jadx hatte sie wie ueblich stillschweigend uebersprungen):
  PlanEvent, PolygonEvent, TravelEvent, TraversalEvent -- inklusive 4 zugehoeriger Enums
  (PlanType, PlanUpcoming, TravelDestination, TraversalType)
- `MissionTimelineEvent` selbst: androguard-bestaetigt GENAU 20 Unterereignis-Felder (nicht 19
  Klassen -- `relocalizing` und `tentativeLocation` teilen sich denselben Typ
  `TentativeLocationEvent`, zwei Felder, eine Klasse)
- `MissionHistoryEntry.timeline` von rohem dict auf `list[MissionTimelineEvent]` umgestellt,
  `parse_mission_timeline()` neu

**Interessanter Nebenbefund**: `PlanEvent.ordered` (Int) -- eine weitere Instanz des `ordered`-Musters,
diesmal eindeutig als Intra-Event-Positionsangabe innerhalb der `upcoming`-Liste, nicht als
Kommando-Sequenzierung. Zusaetzlicher, unabhaengiger Beleg fuer die von ha_roomba_plus schon
frueher korrigierte Lesart von `RoutineCommand.ordered` (siehe Nachtrag elfte Sitzung).

205/205 Tests weiterhin gruen (25 neue), ruff sauber.

## Nachtrag (zwanzigste Sitzung): ERSTER erfolgreicher Live-Lauf ueberhaupt

Ein Nutzer (johnnyh1975 selbst, echtes Prime-Konto, BLID 80B2841450310780) hat
`roombapy-prime-validate` zum ersten Mal in der Geschichte dieses Projekts gegen einen echten
Server laufen lassen. Ergebnis: **7 OK, 1 fehlgeschlagen, 4 uebersprungen.**

**Bestaetigt live, zum ersten Mal ueberhaupt:**
- Die komplette Login-Kette (Discovery -> Gigya -> iRobot-Auth) funktioniert gegen einen echten Server
- MQTT-Verbindung (AWS-IoT-Custom-Authorizer) funktioniert
- `get_state()` (klassischer Shadow) funktioniert
- `get_favorites()`, `get_mission_history()`, `get_user_households()`, `get_active_map_versions()`
  funktionieren alle REST-seitig

**Eine Fehlermeldung, aber keine neue Erkenntnis noetig -- sie bestaetigt eine bereits
dokumentierte Vorhersage:** `get_settings()` (der benannte "rw-settings"-Shadow) lief in den
Timeout. Der Docstring dieser Methode sagte bereits vorher: "antwortet nur auf SMART-Tier, laeuft
auf EPHEMERAL in den Timeout (kein Fehler)". Falls der Testnutzer ein EPHEMERAL-Tier-Geraet hat
(aeltere Prime-Generation), ist das die erste LIVE-Bestaetigung dieser strukturellen Vorhersage,
kein Bug.

**Ein echter, konkreter Bug gefunden und behoben:** Der Nutzer meldete, sein Roboter reinige seit
Monaten nach Zeitplan -- die Diagnose "keine aktive Kartenversion gefunden" konnte also nicht
stimmen. Ursache gefunden: `diagnostics.py` suchte nach den Feldern `p2mapId`/`id` in der
`get_active_map_versions()`-Antwort -- aber `rest_client.py`s EIGENER Docstring fuer dieselbe
Methode dokumentierte bereits seit der allerersten Sitzung, dass die Antwort mindestens `mapId`
und `mapVersionId` enthaelt. Reiner Eigenfehler im Diagnoseskript, nicht im REST-Client selbst.
Behoben: `mapId` als primaeres Feld ergaenzt, zusaetzliche Debug-Ausgabe (zeigt die tatsaechlichen
Schluessel der Antwort), falls kuenftig wieder kein bekanntes Feld greift.

Das ist der erste Beweis, dass das Diagnoseskript selbst als Werkzeug funktioniert -- es hat
sofort einen echten, konkreten, behebbaren Fehler aufgedeckt, genau wie beabsichtigt.

## Nachtrag (einundzwanzigste Sitzung): Diagnoseskript erweitert, auf Nachfrage

Direkt nach dem ersten Live-Ergebnis um vier Dinge ergaenzt, die beim naechsten Lauf mehr
Information liefern sollen:

1. **Drei neue, sichere Lesezugriffe** (seit ihrer Einfuehrung nie ins Diagnoseskript
   aufgenommen): `get_robot_parts()`, `get_serial_number_data()`, `get_notifications()`.
2. **Automatische Geraeteinfo-Extraktion** (`_report_device_info()`): versucht, Modell/SKU,
   Firmware-Version, Name und Faehigkeiten-Feld aus `get_state()`s Antwort zu lesen (Kandidaten-
   Feldnamen sind Vermutungen, nie an einer echten Antwort verifiziert) -- meldet IMMER
   zusaetzlich alle tatsaechlich vorhandenen Top-Level-Schluessel der Antwort, damit ein falscher
   Kandidat beim naechsten Lauf korrigiert werden kann, statt stillschweigend nichts zu finden.
3. **Explizite Tier-Vermutung** (`_report_tier_inference()`): macht die "SMART vs. EPHEMERAL"-
   Ableitung aus `get_settings()`s Erfolg/Fehlschlag als eigenen, klar lesbaren Bericht-Eintrag
   sichtbar, statt nur implizit aus einem FEHLGESCHLAGEN-Eintrag ablesbar zu sein.

Direkter Auslöser: der erste Live-Nutzer musste manuell nach seinem Robotermodell gefragt werden,
um die Tier-Vermutung zu pruefen -- das sollte das Skript kuenftig selbst herausfinden.

210/210 Tests gruen (16 neue fuer die beiden neuen Hilfsfunktionen), ruff sauber. Rauchtest mit
ungueltigen Zugangsdaten weiterhin sauber (Login-Gate greift vor den neuen Pruefungen, kein
Regressionsrisiko).

## Nachtrag (zweiundzwanzigste Sitzung): dieselbe Luecke bei household_id gefunden und behoben

Auf Nachfrage ("brauchen wir noch weitere Diagnose-Details?") systematisch geprueft, wo sonst noch
dieselbe Art Fehler wie beim Karten-Bug lauern koennte: geratene Feldnamen ohne Debug-Fallback bei
Fehlschlag. Gefunden: `household_id = _extract_first_id(households, ["householdId", "id"])` fuer
den Zeitplan-/DND-Pfad hat exakt dasselbe Risiko -- `get_user_households()` ist selbst als
Analogie/unbestaetigt dokumentiert, die Feldnamen sind reine Vermutung.

Behoben mit einer neuen, wiederverwendbaren `_shallow_summary()`-Hilfsfunktion: fasst eine
unbekannte Antwortstruktur fuer die Debug-Ausgabe zusammen (Schluessel + Werttypen, NIE
tatsaechliche Werte -- bewusst so, damit auch bei unerwarteten Antwortformen keine potenziell
sensiblen Daten wie Adressen oder Namen in einem geteilten Bericht landen). Sowohl die
Karten-ID- als auch die household_id-Extraktion nutzen jetzt denselben Mechanismus, statt
zweier leicht unterschiedlicher Ad-hoc-Loesungen.

214/214 Tests gruen (4 neue fuer `_shallow_summary`, davon einer explizit gegen Werte-Leckage),
ruff sauber. Rauchtest weiterhin unauffaellig.

## Nachtrag (dreiundzwanzigste Sitzung): zweiter Live-Lauf -- Tier-Vermutung live bestaetigt

chairstacker hat nach Entfernen eines alten Roomba 675 vom Account erneut getestet: **8 OK, 0
fehlgeschlagen, 4 uebersprungen** -- ein sauberer Lauf. Wichtigste Bestaetigung: `get_settings()`
(der benannte "rw-settings"-Shadow) hat diesmal geantwortet -- der vorherige Timeout lag also
tatsaechlich am falschen (alten, stillgelegten) BLID, nicht an einer echten Tier-Einschraenkung
des aktuellen Geraets. Die vorher zurueckgenommene Tier-Vermutung fuer DIESES konkrete Geraet
(Roomba 405, SKU G185020, Firmware p25-405+9.3.7+I4.6.150 -- via dorita980 bestaetigt, nicht aus
roombapy-prime selbst) ist damit gegenstandslos -- es antwortet, ist also SMART-Tier-faehig.

**Kartenversions-Problem besteht weiterhin, trotz des Feldnamen-Fixes aus der letzten Sitzung.**
Wahrscheinlichste Erklaerung: chairstacker hat vermutlich noch die Version VOR dem Fix laufen
lassen (kein `git pull`/Neuinstallation zwischen den Laeufen). Um das fuer den naechsten Lauf in
JEDEM Fall aufzuklaeren (auch falls `get_active_map_versions()` tatsaechlich eine leere Liste
liefert, was der Feldnamen-Fix nicht beheben wuerde), wurde die Debug-Ausgabe erweitert: der
Skip-Text bei "keine aktive Kartenversion gefunden" zeigt jetzt IMMER die tatsaechliche
Antwortstruktur (leere Liste vs. Daten mit unbekannten Feldern sind jetzt unterscheidbar).

214/214 Tests weiterhin gruen, ruff sauber.

## Nachtrag (vierundzwanzigste Sitzung): Diagnoseskript-Abdeckung geprueft, echte Luecken geschlossen

Auf Nachfrage ("testen wir eigentlich die volle Funktionalitaet?") systematisch mit `comm` gegen
`prime_robot.py`s gesamten Methoden-Katalog abgeglichen. Ergebnis: NEIN, nicht vollstaendig --
aber die Luecken waren zum Teil beabsichtigt (alle schreibenden/destruktiven Operationen: Zeitplan-
CRUD, `set_dnd_settings`, `set_setting`, `reset_robot`, `edit_map`, `send_mission_command` --
bleiben bewusst aussen vor, siehe Sicherheitsprinzip) und zum Teil reines Versehen:
`get_live_map_stream()` und `watch_state()` sind beide rein lesend, wurden aber nie einbezogen.
Beide jetzt ergaenzt -- `watch_state()` zeitlich auf 3 Sekunden begrenzt (kein Delta zu bekommen
gilt als OK, nicht als Fehler, da der Roboter sich dafuer aktiv aendern muesste).

**Groessere Ergaenzung: `--dump-config PATH`.** Auf die Frage "koennen wir nicht auch eine
Diagnose-Config-Datei wie bei einer Integration zurueckmelden" hin umgesetzt -- direkt inspiriert
von Home Assistants "Diagnose herunterladen"-Funktion. Anders als der normale Bericht (der nur
Pass/Fail zeigt und automatisch in den Issue-Link einfliesst) speichert diese Datei die
TATSAECHLICHEN Rohantworten aller Lese-Endpunkte als JSON -- echte Feldnamen UND echte Werte,
genau das, was beim chairstacker-Kartenbug gefehlt hat. Zweistufige Redaktion (Zugangsdaten +
offensichtlich sensible Feldnamen wie Adresse/GPS/WLAN-Zugangsdaten), aber bewusst NICHT so
umfassend wie beim normalen Bericht -- diese Datei wird deshalb NIE automatisch Teil des
Issue-Links, sondern muss bewusst einzeln angehaengt werden. Kartenbuendel-Inhalte werden nie
mitgeschrieben (nur Dateinamen) -- ein Wohnungsgrundriss ist persoenlicher als die meisten anderen
hier erfassten Daten.

Umgesetzt ueber eine kleine Erweiterung von `_try()` (optionaler `capture`-Parameter, der
erfolgreiche Rohergebnisse in ein separates dict ablegt, getrennt vom eigentlichen Bericht) plus
eine neue `_redact_raw_capture()`-Funktion.

221/221 Tests gruen (7 neue), ruff sauber. Rauchtest mit `--dump-config` bestaetigt: kein Absturz,
korrekte (leere) JSON-Datei bei fehlgeschlagenem Login.

## Nachtrag (fuenfundzwanzigste Sitzung): erste --dump-config-Datei ausgewertet -- mehrere echte Bugs UND Modellkorrekturen

chairstacker hat die aktualisierte Skriptversion inklusive `--dump-config` gegen denselben (jetzt
bestaetigt korrekten) Roomba 405 laufen lassen und die vollstaendige, echte JSON-Rohantwort
geteilt (per privater Nachricht, nicht oeffentlich, wie in `--dump-config`s Warnung empfohlen).
Das ist die ergiebigste einzelne Datenquelle seit `base_roomba_config.json` selbst.

**Definitiv geloest: der Kartenversions-Bug.** Die echte `get_active_map_versions()`-Antwort
zeigt die tatsaechlichen Feldnamen: `p2map_id`, `entity_type`, `create_time`, `robot_id`, `sku`,
`active_p2mapv_id`, `last_p2mapv_ts`, `state`, `visible`, `name`, `rooms_metadata` -- KEINS davon
war `mapId`/`mapVersionId`, der urspruenglichen (falschen) Dokumentationsannahme aus der ersten
Sitzung. `diagnostics.py` und `rest_client.py`s Docstring korrigiert.

**Geraeteinfo-Extraktion war strukturell falsch.** Die echte `get_state()`-Antwort zeigt: `sku`
liegt unter `payload["state"]["reported"]["sku"]`, nicht auf Top-Level. `_report_device_info()`
entsprechend korrigiert (liest jetzt zusaetzlich `state.reported`, meldet bei Fehlschlag beide
Ebenen an Schluesseln).

**Zwei echte Korrekturen am Kernmodell, beide aus echten Missionshistorie-Daten:**
- `RegionType`s Werte sind KLEINGESCHRIEBEN ("rid"/"zid"), nicht gross wie urspruenglich aus
  Bytecode gelesen ("RID"/"ZID") -- die Konstantennamen im Bytecode stimmten, die tatsaechliche
  Serialisierung nicht. Korrigiert, Python-Member-Namen bleiben gross (nur Werte geaendert).
- `CommandParams.scrub`s Wire-Schluessel ist tatsaechlich `"swScrub"`, nicht `"scrub"` --
  ebenfalls korrigiert (Python-Attributname bleibt "scrub" fuer Abwaertskompatibilitaet).

**Zwei neue Felder ergaenzt**, beide aus echten Daten, vorher unbekannt:
- `CommandParams.operating_mode` (Wire: "operatingMode", beobachtete Werte 2/32)
- `RoutineCommand.initiator` (Wire: "initiator", beobachtete Werte "cloud"/"rmtApp" -- wer/was
  die Mission ausgeloest hat)

**Ein wichtiger Ruecknahme: die "SMART-Tier live bestaetigt"-Behauptung aus der dreiundzwanzigsten
Sitzung war verfrueht.** Derselbe Nutzer, dasselbe Geraet (SKU G185020), zwei Laeufe kurz
hintereinander -- einmal `get_settings()` erfolgreich, einmal Timeout. Das ist kein stabiles
Tier-Signal. Docstrings und die automatische Tier-Vermutungsausgabe in `diagnostics.py`
entsprechend vorsichtiger formuliert ("deutet auf" statt "ist", mit explizitem Hinweis auf die
beobachtete Inkonsistenz). Offene Hypothese, nicht Code-Fix: moeglicherweise muss der Roboter
selbst aktiv mit AWS IoT verbunden sein, damit ein benannter Shadow antwortet, waehrend der
klassische Shadow eventuell aus einem Cache bedient wird -- ungeklaert.

**Ein neuer, echter, ungeloester Bug:** `get_notifications()` schlaegt live mit HTTP 400 fehl. Die
URL selbst stimmt mit `base_roomba_config.json` ueberein -- vermutlich liegt es am
Platzhalter-`app_version`-Wert ("1.0") oder einem fehlenden, in der Konfigurationsdatei nicht
sichtbaren Parameter. Docstring aktualisiert, als bekannter offener Fehler markiert, kein Fix
versucht ohne weitere Daten.

**Reichhaltige Missionshistorie-Rohdaten bestaetigen zusaetzlich** (ohne Code-Aenderung noetig,
nur zur Kenntnisnahme): `RoutineCommand`s bestehende Feldzuordnungen (`command`, `p2map_id`,
`ordered`, `user_p2mapv_id`, `regions[].region_id`/`type`) sowie mehrere `CommandParams`-Felder
(`twoPass`, `suctionLevel`, `carpetBoost`) stimmen 1:1 mit der Dokumentation ueberein -- ein gutes
Zeichen fuer die Zuverlaessigkeit der urspruenglichen nativen Analyse insgesamt, trotz der oben
genannten Einzelkorrekturen.

228/228 Tests gruen (12 neue/aktualisiert), ruff sauber.

## Nachtrag (sechsundzwanzigste Sitzung): der Rest der --dump-config-Datei -- vollstaendige Modelle fuer zwei bisher rohe Endpunkte

chairstacker hatte die 38k-Zeichen-Datei ueber zwei private Nachrichten geteilt; der erste Teil
wurde in der fuenfundzwanzigsten Sitzung ausgewertet, der zweite Teil (ab der Mitte der
Verschleissteile-Liste) hier. Enthielt die vollstaendigen, echten Antworten fuer
`get_serial_number_data()` und `get_active_map_versions()` -- beide bisher nur als rohes JSON
durchgereicht, jetzt vollstaendig typisiert.

**`get_active_map_versions()`**: Neue Modelle `P2MapVersion` und `RoomMetadataEntry` plus
`parse_active_map_versions()`. Bestaetigt: ein Account kann mehrere Karten haben (im
beobachteten Fall zwei: "Whole House" und "Master_Bathroom"). Der wertvollste Einzelfund dabei:
`rooms_metadata[].room_metadata.operating_mode_defaults` ist ein Dict (Schluessel = Operating-
Mode-ID als String, z.B. "512"/"32"/"2"), dessen WERTE direkt CommandParams-foermig sind --
`CommandParams.from_json()` laesst sich unveraendert wiederverwenden. Bestaetigt ausserdem, dass
`region_type` konsistent kleingeschrieben ist ("rid"/"zid", passend zum Fix aus der letzten
Sitzung) und dass manche Raeume einen nutzervergebenen Namen haben (z.B. "Bathroom"), andere
nicht.

**`get_serial_number_data()`**: Neues Modell `RobotSerialInfo`. Bestaetigt u.a. `family: "Roomba
Combo"` (ein Saug+Wisch-Kombigeraet), `series: "G1"`, sowie den nutzervergebenen Robotername
("House_Bot").

**Nebenbei eine unvollstaendige Verdrahtung entdeckt und geschlossen:** `CommandParams.routine_type`
existierte bereits als Feld (samt Docstring, der bereits auf chairstackers Daten verwies), war
aber nie an `to_json()`/`from_json()` angebunden -- vervollstaendigt.

235/235 Tests gruen (7 neue), ruff sauber.

## Nachtrag (siebenundzwanzigste Sitzung): detailliertes Review auf Nachfrage -- ein grosser, bisher uebersehener Fund

Auf die Frage "hast du alles verarbeitet, nochmal im Detail reviewen" wurde `MissionHistoryEntry`
und `MissionCommandRecord` systematisch gegen dieselbe echte Missionshistorie erneut geprueft, die
schon in der fuenfundzwanzigsten Sitzung vorlag -- dort war der Fokus auf den NEUEN Modellen
(P2MapVersion etc.), die eigenen, laengst bestehenden Feldzuordnungen wurden nicht erneut
gegengeprueft. Ergebnis: **fast alle Feldnamen in beiden Klassen waren falsch geraten.**

**`MissionHistoryEntry`, korrigiert:**
- `robotId` -> `robot_id`
- `minutesRunning`/`minutesPaused`/`minutesCharging`/`minutesDone` -> `runM`/`pauseM`/`chrgM`/`doneM`
- `squareFeetCovered` -> `sqft`
- `numberOfEvacuations` -> `evacs`
- `endedOnDock` -> `eDock`
- `doneCode`/`doneRaw` -> `done`/`done_raw` (beide scheinen denselben Wert doppelt zu fuehren)
- Der Missionsbefehl selbst steht unter dem Schluessel `cmd`, nicht `command`

**`MissionCommandRecord`, korrigiert:**
- `mapId` -> `p2map_id`, `mapVersionId` -> `user_p2mapv_id`
- `regions` von roher Liste auf `list[Region]` umgestellt (Struktur inzwischen bekannt)

**`Region.from_json()` fehlte komplett** (nur `to_json()` existierte, da urspruenglich nur fuers
Senden gebaut) -- ergaenzt. Echte Daten zeigen beim Lesen den Schluessel `region_id`, nicht `id`
wie beim Senden ueber `to_json()` -- moeglicherweise zwei unterschiedliche Wire-Formen fuer
denselben Zweck, beide werden jetzt akzeptiert.

**Ein zweites Vorkommen desselben Gross-/Kleinschreibungs-Musters wie bei `RegionType`:**
`DoneCode.OK` war als `"OK"` bestaetigt (androguard-Konstantenname), echte Daten zeigen `"ok"`
(kleingeschrieben). Alle 19 Werte auf Kleinschreibung umgestellt -- nur "ok" ist direkt bestaetigt,
der Rest folgt demselben, jetzt zweimal beobachteten Muster (durchgaengige Kleinschreibung
wahrscheinlicher als gemischte Schreibung innerhalb eines Enums). **Methodische Konsequenz:**
saemtliche andere ueber androguard "bestaetigten" Enums in dieser Bibliothek (CleaningMode,
VacuumPowerLevel, PadCategory, RankOverlap, CoverageStrategy, PlanType, PlanUpcoming,
TravelDestination, TraversalType) tragen jetzt dasselbe Risiko einer Gross-/Kleinschreibungs-
Abweichung, bis echte Daten dafuer vorliegen -- `_enum_or_none()` faengt das zwar ab (kein Absturz,
Fallback auf rohen String), aber niemand sollte sich derzeit auf die exakte Gross-/Kleinschreibung
dieser konkreten Enum-Werte verlassen.

**Zwei kleinere Ergaenzungen aus demselben Datensatz:**
- `CommandParams.no_auto_passes` (Wire: "noAutoPasses") -- gefunden an einer ungewoehnlichen
  Stelle: eingebettet als string-serialisiertes (Python-repr-artiges, kein direktes JSON)
  `cmdStr`-Feld in `get_state()`s `cleanSchedule2`-Liste.
- Neue Modelle `RobotPart`/`RobotPartsInfo` fuer `get_robot_parts()` (bisher rohes JSON).

**Bewusst weiterhin NICHT modelliert**, zur Kenntnisnahme fuer kuenftige Sitzungen:
- `get_state()`s `cap`-Objekt (35 Faehigkeits-Flags/-Stufen wie `carpetBoost: 3`, `suctionLvl: 4`,
  `maps: 6`) -- reichhaltig, aber ein eigenes Modell waere ein groesseres Vorhaben fuer sich
- `cleanSchedule2` selbst als Ganzes (die im Shadow eingebettete Zeitplanform, separat von den
  REST-basierten `get_schedules()`/`ScheduleOptions`) -- nur das einzelne `no_auto_passes`-Feld
  daraus wurde uebernommen
- Diverse MissionHistoryEntry-Felder ohne erkennbaren Mehrwert fuer eine Home-Automation-
  Bibliothek (`wlBars`, `startEndWlBars`, `oModeStats`, `saves`, `wifiChannel`, `flags`, `chrgs`,
  `pauseId`, `nMssn`) -- bleiben ueber `.raw` weiterhin zugaenglich

242/242 Tests gruen (14 neue), ruff sauber.

## Nachtrag (achtundzwanzigste Sitzung): noch einmal Zeichen fuer Zeichen geprueft, auf explizite Nachfrage

Zwei weitere, kleinere aber echte Funde beim erneuten, diesmal zeichenweisen Durchgehen der
kompletten Antwort:

**`get_state()` enthaelt entgegen meiner bisherigen Annahme GAR KEIN Firmware-Feld.** Die
vollstaendige `reported`-Struktur hat genau acht Schluessel (`digiCap`, `nsmip`, `cap`,
`cleanSchedule2`, `schedHold`, `sku`, `svcEndpoints`, `soldAsSku`) -- keiner davon ist eine
Firmware-/Softwareversion. `_report_device_info()`s "firmware"-Kandidatensuche wird hier also
zuverlaessig leer bleiben, nicht weil etwas falsch waere, sondern weil das Feld schlicht nicht in
dieser Antwort steckt. Firmware kommt stattdessen aus `get_serial_number_data()` oder aus
Missionshistorie-Eintraegen (beide fuehren `softwareVer`). Docstring entsprechend klargestellt,
damit ein leeres Ergebnis hier nicht als Fehler missverstanden wird.

**Eine bestaetigte Querverbindung, rein informativ:** `get_state()`s `svcEndpoints.svcDeplId`
("v007") stimmt exakt mit dem Praefix in `get_live_map_stream()`s MQTT-Topic ueberein
("v007-irbthbu/things/.../livemap/update"). Bestaetigt, dass dieses Praefix kein Zufallswert ist,
sondern aus der "Deployment-ID" des Accounts/Geraets stammt -- nuetzlich, falls das Live-Map-Topic
jemals fuer ein anderes Geraet/Deployment konstruiert werden muss, statt es woertlich zu
uebernehmen.

**Zusaetzlich bemerkt, bewusst nicht geaendert:** Die in `cleanSchedule2[].cmdStr` eingebettete
Kommandostruktur nutzt `pmap_id`/`user_pmapv_id` (OHNE die "2"), waehrend ueberall sonst bestaetigt
`p2map_id`/`user_p2mapv_id` gilt. Da `cleanSchedule2` ohnehin als Ganzes unmodelliert bleibt (siehe
vorheriger Nachtrag), keine Code-Aenderung noetig -- aber falls diese Struktur spaeter doch
modelliert wird, ist das ein wichtiger, eigener Namenskonvention-Unterschied, keine Tippfehler-
Verwechslung.

**Ehrliche Einschaetzung nach zwei Durchgaengen:** Kein weiterer, aehnlich grosser Fund wie die
Feldnamen-Korrekturen der siebenundzwanzigsten Sitzung aufgetaucht. Die verbleibenden, bewusst
unmodellierten Bereiche (cap-Objekt, cleanSchedule2 als Ganzes, diverse Missionshistorie-
Nebenfelder) sind bereits benannt, nicht uebersehen. Sicher sein kann ich trotzdem nicht zu
100% -- die einzige Methode, die bisher tatsaechlich Bugs gefunden hat, war der Abgleich gegen
echte Daten, nicht das erneute Lesen des eigenen Codes; weitere echte Antworten (andere
Endpunkte, andere Geraete) wuerden vermutlich weitere, aehnliche Fehler aufdecken, so wie es
bisher bei jeder neuen Datenquelle der Fall war.

242/242 Tests weiterhin gruen, ruff sauber.

## Nachtrag (neunundzwanzigste Sitzung): derselbe Bugtyp bei household_id gefunden -- trotz laengst vorliegender Daten uebersehen

Auf nochmalige Nachfrage ("kannst du noch etwas finden") den Haushaltslisten-Teil der laengst
vorliegenden echten Antwort erneut geprueft -- diesmal gezielt, nicht nur oberflaechlich als
"bleibt roh" abgehakt. Ergebnis: **derselbe Fehlertyp wie beim Karten-Bug, diesmal bei
`household_id`**, und er war die ganze Zeit sichtbar, wurde aber nicht mit derselben Sorgfalt
gegengeprueft wie die Missionshistorie-Felder.

`diagnostics.py`s `_extract_first_id(households, ["householdId", "id"])` sucht nach zwei
camelCase-/generischen Kandidaten -- die tatsaechliche, laengst bekannte Antwort zeigt
`"household_id"` (snake_case), keiner der beiden Kandidaten passt. Das haette den Zeitplan-/DND-
Pruefungspfad im Diagnoseskript genauso stillschweigend blockiert wie zuvor bei den Karten.
Behoben: `"household_id"` als erster Kandidat ergaenzt.

**Bei der Gelegenheit ein vollstaendiges Modell fuer `get_user_households()` gebaut**
(`Household`/`HouseholdRobot`/`HouseholdUser` + `parse_user_households()`), da die Struktur jetzt
ohnehin vollstaendig bekannt ist. Nebenbei die Docstring-Einschaetzung korrigiert: der Endpunkt
war als "im aktuellen App-Code totes Gewebe, HTTP-Methode nur Konvention" dokumentiert --
funktioniert aber live einwandfrei. "Im App-Code unbenutzt" bedeutete hier tatsaechlich nur "diese
App-Version braucht es nicht", nicht "der Server unterstuetzt es nicht mehr".

246/246 Tests gruen (4 neue), ruff sauber.

## Nachtrag (dreissigste Sitzung): ein fehlendes Feld, kein falscher Name diesmal

Auf nochmalige Nachfrage ("und was noch") gefunden: `MissionCommandRecord` hatte kein
Top-Level-`params`-Feld -- getrennt von `regions[].params`, in der echten Missionshistorie mal
gesetzt (z.B. `{"profile": "light"}`, beobachtet bei `initiator: "rmtApp"`-Eintraegen), mal
explizit `null` (bei mehreren `initiator: "cloud"`-Eintraegen). Anders als die bisherigen Funde
dieser Sitzungsreihe war das kein falsch geratener Feldname, sondern ein komplett fehlendes Feld
-- die Daten dafuer lagen die ganze Zeit vor, wurden aber nie einzeln herausgezogen. Ergaenzt,
nutzt `CommandParams.from_json()` wie das analoge `regions[].params`.

247/247 Tests gruen (1 neu), ruff sauber.

## Nachtrag (einunddreissigste Sitzung): programmatischer Vollabgleich statt manuellem Lesen -- der bisher groesste Fund

Auf explizite Kritik ("das ist zu iterativ, hast du wirklich die vollen Informationen geprueft")
wurde die Methode geaendert: statt die Daten nochmal von Auge zu lesen, wurden ALLE Feldnamen aus
der kompletten `diagnose.json` (beide Nachrichten, als echtes Python-Objekt rekonstruiert)
programmatisch rekursiv extrahiert und gegen jeden `.get()`-Aufruf im Code gehalten. Ergebnis: der
bisher folgenreichste Fund der gesamten Untersuchung.

**Die komplette MissionTimelineEvent-Verarbeitung aus der achtzehnten Sitzung war bis zu diesem
Zeitpunkt komplett wirkungslos.** `parse_mission_timeline()` suchte nach dem Schluessel `"events"`
innerhalb von `timeline` -- dieser Schluessel existiert in echten Daten schlicht nicht. Die
tatsaechlichen, reichen Unterereignisse stehen unter `"finEvents"`; eine separate, sparsame
`"event"`-Liste (nur `type`+`ts`, kein Zusatzobjekt) existiert daneben und enthaelt keine
zusaetzliche Information. Jede einzelne Mission haette bei jedem bisherigen Nutzer eine leere
`.timeline`-Liste geliefert, ohne Fehler -- der Bug war vollstaendig stumm.

**Zusaetzlich, an fast jedem Unterereignistyp: systematisch falsche Feldnamen**, alle demselben
Muster folgend (Wire-Format nutzt kurze `p2map`-praefigierte Formen, nicht die verboseren
camelCase-Vermutungen):
- `RoomEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `regionId`->`rid`
- `TravelEvent`: `destination`->`dest`, `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`,
  `regionId`->`rid`, `zoneId`->`zid`
- `TraversalEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `regionId`->`rid`,
  `zoneId`->`zid`
- `ZoneEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `zoneId`->`zid`
- `TentativeLocationEvent`: `confirmedMapId`->`confp2mapId`, `confirmedMapVersion`->`confp2mapvId`,
  `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`. Zusaetzlich: der MissionTimelineEvent-Schluessel
  selbst ist `"reloc"`, nicht `"relocalizing"` oder `"tentativeLocation"` wie urspruenglich
  angenommen -- ergaenzt, ohne die beiden alten (unbestaetigten) Feldnamen zu entfernen.
- `PadWashEvent`: `fluidAmount`->`flAmt`, `padWashState`->`pwState`
- `MissionTimelineEvent.start_time`/`end_time` selbst: `startTime`/`endTime` existieren nicht,
  echte Schluessel sind `ts`/`ets`

**Zwei weitere, dabei entdeckte Enum-Fehlschreibungen** (dasselbe Gross-/Kleinschreibungsmuster
wie RegionType/DoneCode zuvor): `TravelDestination` und `TraversalType` waren grossgeschrieben,
echte Daten zeigen Kleinschreibung ("dock"/"zone"/"room", "region"). Beide korrigiert.

**Ende-zu-Ende gegen die vollstaendige echte Missionshistorie verifiziert** (nicht nur einzelne
Unit-Tests): alle drei echten Missionen liefern jetzt korrekt befuellte Timelines (8/10/7
Ereignisse), mit korrekt aufgeloesten Karten-IDs, Zonen- und Raum-Referenzen -- vorher ueberall
`0`.

**Ehrliche Einordnung:** Dieser Fund waere durch die vorherige Methode (Feld-fuer-Feld-Lesen mit
gelegentlichen Stichproben) wahrscheinlich nicht aufgefallen -- er lag mehrere Verschachtelungs-
ebenen tief (timeline -> finEvents -> Unterereignis -> Feld) und betraf einen Schluessel, dessen
Abwesenheit keinen Fehler wirft, nur eine leere Liste. Die programmatische Methode (alle
Feldnamen rekursiv extrahieren, gegen alle `.get()`-Aufrufe abgleichen) ist damit die einzige
bisher gefundene Vorgehensweise, die diese Art von stillem, tief verschachteltem Bug zuverlaessig
aufdeckt -- fuer kuenftige Live-Daten-Auswertungen sollte sie der Standardansatz sein, nicht die
Ausnahme.

247/247 Tests gruen (1 neu, mehrere korrigiert), ruff sauber.

## Nachtrag (siebzehnte Sitzung): priorisierte Roadmap -- was als Naechstes

Auf die Frage "was muessen wir an der Library noch machen" systematisch beantwortet. Bei der
Gelegenheit ein weiterer Fund in derselben Konfigurationsdatei: **47 Settings-Kommandos
(`namedShadow: "rw-settings"`) insgesamt, davon ~25 bisher komplett unmodelliert** (SetChildLock,
SetAudioVolumePattern, Pad-Wash-Einstellungen, PMapLearningAllowed, WifiDeviceLocalizationAllowed,
etc.) -- dokumentiert in `docs/API_REFERENCE.md`s neuem "Settings vocabulary"-Abschnitt, bewusst
NICHT implementiert (Feldnamen/Wire-Format pro Setting nicht reverse-engineered, waere ein eigener,
groesserer Aufwand).

**Prioritaet 1 -- der eine Gate-Keeper, der alles andere qualifiziert:**
Mindestens ein Lauf von `roombapy_prime.diagnostics` gegen einen echten Prime/V4-Account. Nichts
in dieser Bibliothek wurde je live getestet. Das ist der Unterschied zwischen "gruendlich
analysiert" und "funktioniert wirklich" -- kein weiterer Analyseaufwand ersetzt das.

**Prioritaet 2 -- konkrete, bekannte Luecken (kein neuer RE-Aufwand, nur Fleissarbeit):**
- Die ~25 neu gefundenen Settings-Kommandos als Methoden/Felder modellieren, SOBALD ihre
  Wire-Form bekannt ist (entweder durch Live-Traffic-Capture oder gezielte native Nachverfolgung
  einzelner Kommandos)
- `HouseholdSettingOptions`-Struktur (aktuell rohes dict)
- Die 16 von 20 noch nicht typisierten `MissionTimelineEvent`-Unterereignistypen

**Prioritaet 3 -- architektonisch bekannt, aber bewusst zurueckgestellt:**
- Teaming/Mehrgeraete-Koordination (9 REST-Endpunkte bestaetigt, dokumentiert in
  API_REFERENCE.md, braucht einen echten Mehrroboter-Haushalt zum sinnvollen Testen)
- V1-Editier-Kommando-Umschlagformat (Diskriminator-Schluessel unbekannt)
- p2maps-Auth-Mechanismus (SigV4-Annahme, strukturell nie aus Primes eigenem Code bestaetigbar,
  siehe C4 im aelteren Abschnitt dieses Dokuments)

**Bewusst nicht geplant:**
- Account-/App-UX-Oberflaeche (Survey-System, Notification-Verwaltung jenseits des Lesens,
  Missionsbild-Freigabe) -- dokumentiert in API_REFERENCE.md, als niedrige Prioritaet fuer eine
  Home-Automation-Bibliothek eingestuft
- Weitere native Vertiefung zur commandDefs-Multi-Entry-Frage (Issue #9, siehe eigener Abschnitt) --
  vier zurueckgenommene "definitive" Schluesse in dieser Untersuchung sprechen dagegen, weitere
  Vtable-Arbeit zu investieren; nur noch echte Feldverifikation vorgesehen

## Nachtrag (fuenfzehnte Sitzung, selber Tag): DEFINITIVE Aufloesung -- die tatsaechliche Konfigurationsdatei gefunden

Auf "suche weiter" hin wurde der Konfigurations-Lookup (`PMIAssetServiceImpl::getProtocolConfig()`)
weiterverfolgt. Dabei zunaechst ein ECHTER METHODENFEHLER in der eigenen Vtable-Lesung gefunden und
korrigiert: die "vtable for X"-Symboladresse ist der Beginn des ABI-Vtable-*Blocks* (inkl.
Offset-zu-Top + RTTI-Header), waehrend Objekte selbst einen um +0x10 verschobenen Zeiger speichern
(bestaetigt aus dem Konstruktor: `add x9, x8, #0x10`, sowie unabhaengig aus der ELF-Relokationstabelle
via `readelf -r`). Die fruehere Lesung war dadurch um 2 Vtable-Slots versetzt -- nach Korrektur zeigte
sich Slot 0xA0 als `PMIAssetServiceImpl::getProtocolConfig()`, nicht `getNetworkInformation()`
(bestaetigt via `readelf -r` Relokationseintrag `R_AARCH64_GLOB_DAT` -> exakt der erwartete
Vtable-Symbolname).

Von dort aus: `getProtocolConfig()` -> `core::ProtocolConfig::ProtocolConfig(string const&)` --
ein KONSTRUKTOR, der einen rohen String entgegennimmt. Aufrufer statisch nicht auflösbar
(daten-getriebener Aufruf, wie zuvor mehrfach gesehen) -- stattdessen wurde nach der zugrundeliegenden
KONFIGURATIONSDATEI in der APK selbst gesucht, nicht mehr im Bytecode.

**Gefunden: `res/raw/base_roomba_config.json`** (in der APK mitgeliefert, jetzt als
[`docs/base_roomba_config_REFERENCE.json`](base_roomba_config_REFERENCE.json) gesichert) --
129 Eintraege in `commandList`, jeder mit `commandId`, `topic`, `namedShadow` (und teils
`httpMethod`/`urlPath` fuer REST-Kommandos). Das ist die **maßgebliche, tatsaechliche
Konfigurationsquelle** fuer den Transportmechanismus jedes einzelnen Kommandos -- keine weitere
Interpretation noetig.

**Definitiver Befund fuer Missionsbefehle:**
```json
{"commandId": "AssetControlCommand", "topic": "cmd", "namedShadow": "", "networkList": ["lss", "awsIot"]}
```
`namedShadow` ist LEER -- Missionsbefehle nutzen den **klassischen (unbenannten) Shadow**, keinen
benannten. Zum Vergleich, im selben JSON:
```json
{"commandId": "SetBinPause", "topic": "delta", "namedShadow": "rw-settings", ...}
{"commandId": "AssetScheduleCommand,Set", "topic": "delta", "namedShadow": "rw-schedule", ...}
```
Settings und Zeitplaene nutzen tatsaechlich benannte Shadows (rw-settings/rw-schedule) --
Missionsbefehle nicht. Das bestaetigt `send_mission_command()`s klassischen-Shadow-Ansatz
DEFINITIV -- die Ruecknahme des "rw-settings"-Fixes in der vierzehnten Sitzung war also korrekt,
jetzt aus Primaerquelle statt aus einer verworfenen Kette bestaetigt.

**Bonusfund im selben JSON:** `ResetRobotCommand` zeigt den REST-Pfad tatsaechlich in Aktion
(`"httpMethod": "POST", "urlPath": "/v1/%s/reset", "networkList": ["awsApiGateway", "lss"]`) --
bestaetigt, dass `ProtocolAdapterRoombaApiGateway` (REST) fuer manche Kommandos wirklich genutzt
wird, andere (wie `AssetControlCommand`) aber MQTT nehmen -- beides koexistiert, pro Kommando
konfiguriert in genau dieser Datei.

**Methodische Lehre:** Dieselbe Lehre wie zuvor (thematische Plausibilitaet reicht nicht), aber
diesmal mit einem zusaetzlichen Baustein: wenn eine native Kette wiederholt auf eine KONSTRUKTOR-
EINGABE per rohem String hinauslaeuft, lohnt es sich, nach der zugrundeliegenden ROHDATEN-DATEI in
der APK selbst zu suchen, statt den Bytecode weiter zu verfolgen -- die eigentliche "Wahrheit" lag
die ganze Zeit in einer mitgelieferten JSON-Datei, nicht im kompilierten Code.

171/171 Tests weiterhin gruen (Docstrings aktualisiert, keine Verhaltensaenderung noetig -- die
Implementierung war schon korrekt), ruff sauber.

## Nachtrag (dreizehnte Sitzung, selber Tag): systematischer Review + Doku-Ausbau

**Dokumentation ergaenzt:** `docs/API_REFERENCE.md` (vollstaendige Methoden-/Modelluebersicht mit
Vertrauensmarkierungen pro Eintrag), `CHANGELOG.md`, `SECURITY.md`, `examples/` (drei lauffaehige
Skripte: `basic_usage.py`, `favorites_and_history.py`, `mission_control.py` mit expliziter
Sicherheitsabfrage vor jedem echten Kommando). Alle Codebeispiele gegen die echte API verifiziert.

**Systematischer Review, drei konkrete Funde:**

1. **Fehlende PrimeRobot-Wrapper**: `delete_map()`, `get_map_geojson_link()`, `download_map_bundle()`
   existierten in `rest_client.py`, aber nie als `PrimeRobot`-Wrapper -- das Diagnoseskript musste
   deshalb auf `robot._rest` zugreifen (privates Attribut). Alle drei ergaenzt, Diagnoseskript
   entsprechend bereinigt. Gefunden durch simplen Abgleich `grep`-Methodennamen in rest_client.py
   gegen `self._rest.`-Aufrufe in prime_robot.py.
2. **Testabdeckungs-Check** (`pytest-cov`) deckte zwei echte Luecken auf:
   - `prime_robot.py` bei 81% -- fast alle duennen REST-Passthrough-Wrapper hatten ueberhaupt
     keinen Test. Tabellengetrieben mit `unittest.mock.create_autospec(PrimeRestClient)` behoben
     (prueft dabei automatisch, dass Aufrufsignaturen zur echten Klasse passen) -- jetzt 95%.
   - `auth.py` bei 55% -- die komplette `login()`-Orchestrierungskette (Discovery -> Gigya ->
     iRobot) war NIE getestet, obwohl das der kritischste Einstiegspunkt der ganzen Bibliothek ist.
     Eine fruehere bewusste Entscheidung ("integrationsfoermig, nicht einheitsfoermig") wurde
     revidiert: eine `_FakeSequentialSession` haelt die drei aufeinanderfolgenden HTTP-Aufrufe nach,
     10 neue Tests decken den Erfolgspfad UND alle "fail loudly"-Validierungsgates ab (fehlende
     Credentials, fehlender einzelner Credential-Schluessel, fehlender mqtt-Endpunkt, Gigya-Fehler,
     der bekannte "mqtt slot"-Rate-Limit-Sonderfall). Jetzt 94%.
   - `mqtt_client.py` (78%) und `diagnostics.py`s `run()` (40%) bewusst NICHT weiter verfolgt --
     echte Netzwerk-/Live-Account-Interna, strukturell so schwer sinnvoll zu mocken wie
     `login()` frueher schien, aber diesmal zurecht: paho-Client-Konstruktion und das eigentliche
     Live-Skript sind integrationsfoermig, nicht einheitsfoermig.
3. Keine TODOs/FIXMEs im Code, keine Rueckgabetyp-Inkonsistenzen gefunden, `examples/` korrekt
   nicht als Package-Daten gefuehrt.

**Gesamtabdeckung: 88% -> 91%. 171/171 Tests gruen, ruff sauber.**

---

## Update (sechste Sitzung, selber Tag): volle Neu-Dekompilierung + sechs neue REST-Bereiche + native Sackgasse geklaert

**Vollstaendige Neu-Dekompilierung** der frisch hochgeladenen APK (2.2.4) durchgefuehrt (24.983 Klassen,
nur 56 Fehler -- alle 56 in EXAKT einer Klassenfamilie, `EditMapV1Request`). Das hat zwei fruehere
Kernannahmen korrigiert und sechs komplett neue REST-Bereiche freigelegt.

### Korrektur 1: V1, nicht V2, ist der aktive Editier-Pfad

`requestEditV2()` wird im gesamten App-Code **kein einziges Mal** aufgerufen -- nur `requestEditV1()`.
Die 9 V1-Kommandos (RenameRoom, SplitRoom, MergeRooms, SetRoomType, SetRoomMetadata,
SetPermanentAreas, DeletePermanentAreas, SetVirtualWalls, AdjustFurniture) sind jetzt in `models.py`
implementiert (bytecode-bestaetigt via androguard, da jadx an genau dieser Klasse scheiterte).
`rest_client.py::edit_map()` nutzt jetzt V1; der alte V2-Pfad ist unter `edit_map_v2()` erhalten,
mit Warnung dass er unbenutzter Code ist.

### Korrektur 2: `FavoriteV1`/Favoriten-Endpunkte vollstaendig, inkl. Bugfix

Alle 5 Favoriten-Endpunkte implementiert. `order_favorite()` hatte einen echten Fehler
(insert_at/insert_before/insert_after im Body statt als Query-Parameter) -- bytecode-bestaetigt
korrigiert.

### Sechs neue REST-Bereiche gefunden und implementiert

Systematische Suche nach allen `urlString`/`"/v1/"`-Mustern im GESAMTEN App-Code (nicht nur
p2maps/favorites) foerderte sechs bisher komplett unbekannte Bereiche zutage:

| Bereich | Endpunkt | Status |
|---|---|---|
| Missionshistorie | `GET /v1/{blid}/missionhistory` | **Vollstaendig implementiert**, alle Query-Parameter bestaetigt |
| Zeitplaene | `GET/DELETE /v1/households/{id}/settings/schedule[/{id}]` | Implementiert, GET/DELETE bestaetigt, POST/PUT fuer create/update angenommen |
| DND-Einstellungen | `GET/PUT /v1/households/{id}/settings/dnd` | Implementiert, beide Methoden bestaetigt |
| Reinigungsprofile | `GET /v1/profiles?assetId=...&p2mapId=...` | Implementiert |
| Standard-Routinen | `GET /v1/p2maps/{id}/routines/defaults` | Implementiert |
| `/v1/user/households` (Haushaltsliste) | -- | **Toter Code** -- nirgends im App-Code aufgerufen, nicht implementiert |

`ScheduleOptions`/`HouseholdSchedule` (die Body-Struktur fuer create/update Zeitplaene) wurden nicht
unter diesem Namen im dekompilierten Baum gefunden -- `create_schedules()`/`update_schedules()`
nehmen daher rohes JSON entgegen statt eine moeglicherweise falsche Struktur vorzugeben.

### Native Sackgasse geklaert (fuer Parallelchat, nicht library-blockierend)

Mehrsitzungen-lange Ghidra-Untersuchung (`FavoriteCommandType::ExecuteMission` -> iteriert
`sendCommand` ueber `commandDefs`?) kam zu einem klaren, wenn auch negativen Ergebnis:
`FavoritesDataUseCaseImpl::executeMissionForFavoriteId` validiert nur die Favoriten-ID, sendet aber
nachweislich kein Kommando (JNI-Bruecke zeigt genau einen virtuellen Aufruf, die aufgerufene Methode
greift nie auf ihr eigenes `FavoriteDataService`-Feld zu). Fuer die Bibliothek nicht blockierend, da
das Wire-Format (`RoutineCommand` -> Shadow-Update) bereits unabhaengig davon vollstaendig bekannt ist.

### Test-Stand nach dieser Sitzung

123/123 Tests gruen, ruff sauber.

### Nachtrag (siebte Sitzung, selber Tag): beide offen gelassenen Punkte doch geschlossen

Auf Nachfrage nochmal gruendlicher gesucht statt vorschnell aufzugeben:

- **ScheduleOptions/HouseholdSchedule/HouseholdScheduleUpdate/ScheduleTime**: existieren doch, jadx
  hatte sie wie EditMapV1Request stillschweigend uebersprungen (nicht in der 56er-Fehlerzahl erfasst).
  Alle Felder via androguard direkt aus der DEX gezogen und vollstaendig in `models.py` implementiert
  (`ScheduleOptions`, `ScheduleTime`, `ScheduleDateEntry`, `ScheduleFrequency`-Enum,
  `HouseholdSchedule`, `HouseholdScheduleUpdate`). `create_schedules()`/`update_schedules()` nehmen
  jetzt die typisierten Modelle statt rohem JSON.
- **`/v1/user/households` (Haushaltsliste)**: bewusst implementiert trotz Totcode-Status in der
  aktuellen App-Version -- eine unbenutzte App-interne Referenz heisst nicht, dass der Endpunkt
  serverseitig nicht existiert. HTTP-Methode (GET) reine REST-Konvention, nicht aus einer
  Request-Klasse bestaetigt (im Gegensatz zu allen anderen hier dokumentierten Endpunkten).

124/124 Tests gruen nach diesem Nachtrag.

## Nachtrag 2 (achte Sitzung, selber Tag): systematischer Vollabgleich DEX vs. jadx-Ausgabe

Auf "suche weiter" hin nicht mehr einzeln nach vermuteten Luecken gesucht, sondern systematisch
ALLE ~11.325 `com.irobot.*`-Klassen aus der DEX gegen den jadx-Ausgabebaum abgeglichen. Ergebnis:
6755 fehlen (nach Ausschluss von R$-Ressourcenklassen/BuildConfig) -- weit ueberwiegend UI-Schicht
(Compose-Screens, Navigation, Fragmente), fuer eine Cloud-Client-Bibliothek irrelevant. Zwei
Untergruppen waren aber hochrelevant:

**`com/irobot/data/missioncommand/datamodels`** (31 fehlende Klassen): komplettes, bisher nie
gesehenes Praeferenz-/Parameter-System fuer Missionsbefehle. `CommandParams` (37 Felder --
Saugkraft, Wischnaesse, Teppich-Boost, Raumbegrenzung, Zeitbox, Fahrgeschwindigkeit fuer
Steuerbefehle, u.v.m.), `Region`/`RegionType`, `CommandPolygon`/`CommandPolygonMetadata`,
`PadWetnessParam`, plus die `MissionPreference`-Familie (CleaningMode, CleaningPasses,
ComboLiquidAmount, LiquidAmount, SoftwareScrub, VacuumPower als Enums). Alles vollstaendig in
`models.py` implementiert, ersetzt die bisherigen rohen dicts in `RoutineCommand.params/regions/
id_multipolys` (abwaertskompatibel -- rohe dicts funktionieren weiterhin daneben).

**`com/irobot/data/restservices/*`** (57 fehlende Klassen, Auswahl bearbeitet): `CreateFavoriteRequest`/
`UpdateFavoriteRequest`/`CreateSchedulesRequest`/`UpdateSchedulesRequest` gefunden und deren
`httpMethod`-Konstruktion direkt aus dem Bytecode gelesen (`const-string "POST"`/`"PUT"` in der
`<init>`-Methode) -- damit sind ALLE vier bisher nur "angenommenen" HTTP-Methoden jetzt
bytecode-bestaetigt: Favorite erstellen=POST, aktualisieren=PUT, Zeitplan erstellen=POST,
aktualisieren=PUT. Alle betroffenen Docstrings aktualisiert.

**Noch nicht bearbeitet, aber gefunden** (fuer eine kuenftige Sitzung): vollstaendige
Missionshistorie-Antwortmodelle (`MissionHistory`, `MissionTimeline`, `MissionTimelineEvent`,
`PlanEvent`, `PolygonEvent`, `TravelEvent`, `TraversalEvent`, `MissionCommand`), `HouseholdSetting`
(Response-Modell fuer DND/Zeitplan-Container), `DNDStatusResponse`/`DNDSchedule.DailySchedule`/
`DNDSchedule.EndsAt`, `Routine`/`RoutineBuilderDefaults`/`RegionDefaults`/`OperatingModeProfile`
(Antwortmodelle fuer Standard-Routinen), `CleaningProfile`/`CleaningProfile.ProfileType`. Aktuell
geben alle betroffenen `get_*`-Methoden weiterhin rohes JSON zurueck -- funktioniert, ist aber nicht
typisiert.

130/130 Tests gruen, ruff sauber nach diesem zweiten Nachtrag.

## Nachtrag 3 (neunte Sitzung, selber Tag): Antwortmodelle fuer Missionshistorie, DND, Reinigungsprofile, Routinen

Die im zweiten Nachtrag gefundenen, aber noch offenen Antwortmodelle jetzt implementiert:

- **`MissionHistoryEntry`/`MissionCommandRecord`** (models.py::parse_mission_history()):
  Top-Level-Felder von `MissionHistory` (Zeiten, `DoneCode`-Enum mit 19 Werten, Flaechendeckung,
  Fehlercode, etc.) typisiert. `timeline` bleibt bewusst rohes JSON -- `MissionTimelineEvent` hat
  20 moegliche Unterereignistypen (CommandEvent, DiscoveryEvent, ErrorEvent, ..., ZoneEvent), von
  denen nur 4 (PlanEvent, PolygonEvent, TravelEvent, TraversalEvent) im Detail bytecode-inspiziert
  wurden -- volle Typisierung aller 20 stand in keinem vertretbaren Verhaeltnis zum Nutzen.
- **`CleaningProfile`** (mit `CommandParams.from_json()` als neuer Kehrfunktion zu `to_json()`).
- **`DNDStatusResponse`** -- WICHTIGER Fund: es gibt ZWEI verschiedene DND-Repraesentationen im
  App-Code (`DNDSchedule`-sealed-class mit DailySchedule/EndsAt-Untertypen fuer den PUT-Request-
  Aufbau, und die flache `DNDStatusResponse` fuer die GET-Antwort) -- beide dokumentiert, nur
  DNDStatusResponse implementiert (die tatsaechliche Antwortform).
- **`HouseholdSetting`** -- settingId/settingType typisiert, `options` bleibt rohes dict
  (HouseholdSettingOptions selbst nicht weiter untersucht, vermutlich polymorph je settingType).
- **`Routine`/parse_default_routines()`** -- fuer get_default_routines(). `commandDefs` bleibt als
  Liste roher dicts (in Analogie zu FavoriteV1.command_defs vermutlich List<RoutineCommand>, aber
  nicht generisch bestaetigbar).

Alle vier zugehoerigen `get_*()`-Methoden in rest_client.py geben weiterhin rohes JSON zurueck
(unveraendertes Verhalten) -- die neuen `parse_*()`/`Klasse.from_json()`-Funktionen sind ein
separater, optionaler Schritt, exakt wie bei `parse_map_bundle()`.

139/139 Tests gruen, ruff sauber nach diesem dritten Nachtrag.

## Update (vierte Sitzung, selber Tag): "brauchen wir noch mehr Dekompilierung?"

**Kurze Antwort: nein, keine weitere Dekompilierung noetig -- aber eine
breitere SUCHE im bereits Dekompilierten war es sehr wohl.** Die vorige
Einstufung von C2 ("nicht wirtschaftlich weiter aufloesbar") war zu
frueh aufgegeben. jadx/dex-Dateien sind in dieser Umgebung nicht mehr
vorhanden (nur die bereits dekompilierten Java-Quellen aus einer
frueheren Sitzung) -- ein Neuversuch mit anderen jadx-Einstellungen war
also ohnehin nicht moeglich. Was stattdessen half: eine systematische
Suche nach ALLEN `urlString = "..."`-Zuweisungen im gesamten p2maps-
Quellbaum (`grep -rn 'urlString = "'`), statt sich auf die eine
fehlgeschlagene Coroutine-Methode zu versteifen.

### tar.gz-Frage vollstaendig aufgeloest

`P2MapGeoJSONRequest.java` (bisher uebersehen) bestaetigt:

    GET /v1/p2maps/{mapId}/versions/{mapVersion}/geojson?response_type=link
    Accept: application/json

liefert (vermutlich) die vorsignierte URL, von der aus
`fetchPersistentMap`/`fetchLatestPersistentMap`/`fetchMissionMap` ihr
tar.gz-Kartenbuendel laden (das war bereits bestaetigt, siehe vorherige
Sitzung). `response_type=binary` (Accept: application/gzip) laedt das
Archiv direkt, ohne Umweg -- hier NICHT implementiert (braeuchte einen
parametrisierbaren Accept-Header im SigV4-Signer). Implementiert als
`get_map_geojson_link()`. Einzig noch offen: welcher JSON-Schluessel in
der "link"-Antwort die eigentliche URL traegt -- keine eigene Response-
Klasse im Quellcode gefunden, nur die Anfrage selbst.

### Zwei weitere, bisher komplett uebersehene Endpunkte gefunden

- **`delete_map()`** -- `DeleteMapRequest.java`: trotz des Namens KEIN
  HTTP DELETE, sondern `POST /v1/p2maps/{id}/settings
  ?trigger_fast_updates=true` mit Body `{"visible": false}` -- ein
  "soft delete" ueber denselben Endpunkt wie `set_map_name()`. Klein,
  implementiert.
- **`EditMapV1Request`** -- eine GANZE PARALLELE Editier-Kommando-
  Vokabular (RenameRoom, AdjustFurniture, SetPermanentAreas,
  DeletePermanentAreas, SplitRoom, MergeRooms, SetRoomType,
  SetVirtualWalls -- 8 Kommandos), separat von der bereits
  implementierten V2-Vokabular (10 Kommandos, teilweise ueberlappend,
  teilweise anders benannt). `P2MapAPIEditRequestor` exponiert beide
  Pfade (`requestEditV1`/`requestEditV2`) als gleichberechtigte
  Alternativen -- vermutlich V1 fuer aeltere Firmware mit
  eingeschraenkterem Funktionsumfang, V2 fuer neuere. Die Dispatch-
  Logik (wer entscheidet wann V1 vs. V2) selbst ist wieder "nicht
  decompiled". **NICHT implementiert** -- neu gefundene, echte Luecke,
  vom Umfang her vergleichbar mit der bereits gebauten V2-Vokabular,
  bewusst nicht in dieser Sitzung noch mit reingenommen.

### Was das ueber die Restarbeit sagt

Diese Sitzung zeigt: die verbleibenden C2-artigen Luecken sind nicht
alle gleich hart. Manche (wie diese) sind reine "nicht breit genug
gesucht"-Luecken, andere (Missionssteuerungs-Dispatch, p2maps-Auth-
Mechanismus) sind echte native Grenzen. Vor dem naechsten "das ist
nicht aufloesbar"-Schluss lohnt sich ein systematischer Grep ueber den
gesamten Quellbaum nach dem gesuchten Muster (hier: URL-Fragmente),
nicht nur ein gezielter Blick auf die eine Methode, die als Erstes
fehlschlug.

---
## Update (dritte Sitzung, selber Tag): native Disassemblierung + Rest

**Werkzeuge:** `binutils-aarch64-linux-gnu` nachinstalliert (apt),
damit `aarch64-linux-gnu-objdump -d` echte ARM64-Disassemblierung
liefert (Standard-objdump auf diesem x86-64-System konnte das nicht).
Kein Ghidra/IDA verfuegbar -- reine Rohdisassemblierung, Strings-
Suche und manuelle ADRP/ADD-Adressverfolgung, kein automatischer
Pseudocode.

### Durchbruch: Missionssteuerung IST implementierbar (C1 halb revidiert)

Vorherige Einstufung ("strukturell harte native Grenze, nicht
schliessbar") war nur zur Haelfte richtig:

- **Transport bestaetigt** via woertlichem Format-String in
  `liblegacyCore.so`: `$aws/things/%s/shadow/update` (Adresse
  0xde2a3a, gefunden ueber Cross-Referenz-Suche der ADRP/ADD-
  Instruktionspaare, die auf diese Adresse zeigen). Kommandos laufen
  ueber den bereits implementierten Shadow-update()-Pfad, nicht ueber
  einen separaten Topic -- deckt sich mit der alten, nie bestaetigten
  Vermutung aus CLOUD_SHADOW_PUSH_FINDINGS.md.
- **Payload-Form bestaetigt** aus Kotlin-Quellcode (nicht nativ!):
  `CommandWrapper` (@Serializable, ein Feld `cmd` mit
  @SerialName("cmd")) wrapt `RoutineCommand` (@Serializable, alle
  Feldnamen per @SerialName direkt aus dem Quellcode, nicht geraten).
  `CommandType`-Enum-Werte ebenfalls per @SerialName bestaetigt,
  inklusive zweier ueberraschender Abweichungen von den Kotlin-
  Konstantennamen (CLEAN_SPOT -> "point_clean", nicht "clean_spot").
- **Implementiert**: `models.py` (MissionCommandType, RoutineCommand),
  `prime_robot.py::send_mission_command()`.
- **Weiterhin offen**: der native `postCommand()`-Dispatch selbst
  wurde nicht bis zum tatsaechlichen MQTT-Publish zurueckverfolgt --
  mehrere Ebenen nicht-exportierter, symbolloser statischer Funktionen,
  mit den verfuegbaren Werkzeugen nicht wirtschaftlich weiter
  aufloesbar. Die hier dokumentierte Huelle kombiniert zwei
  unabhaengig bestaetigte Fakten, nie GEMEINSAM live getestet.

### `irbt_topic_prefix`: Existenz doppelt bestaetigt, Inhalt weiterhin offen

Gefunden: `core::ServiceDiscoveryImpl::kIrbtTopicPrefixFieldName` /
`kIotTopicPrefixFieldName` als echte Symbole (BSS-Sektion, `std::string`-
Objekte mit statischer Initialisierung). Cross-Referenz-Suche auf die
Initialisierungsstelle blieb erfolglos (vermutlich in einer nicht
exportierten Funktion, die ueber die verfuegbaren Adressbereiche nicht
gefunden wurde). Die FELDNAMEN-KONSTANTEN existieren also nachweislich
-- der literale JSON-Schluessel-STRING bleibt unbestaetigt.

### Sonstige Aenderungen diese Sitzung

- **Nebenlaeufigkeitsschutz**: `self._client_lock` (`threading.Lock`)
  in `mqtt_client.py` -- schliesst die vorher dokumentierte Luecke
  zwischen `replace_token()` und `get_shadow()`/`update_shadow()`.
  Mit echtem Multi-Thread-Test verifiziert (inkl. Gegenprobe: Test
  schlaegt nachweislich fehl, wenn der Lock durch ein No-Op ersetzt
  wird).
- **Backpressure-Fehlersichtbarkeit**: verworfene Exception-Eintraege
  werden jetzt als ERROR statt WARNING geloggt (verhindert den Verlust
  nicht, macht ihn aber sichtbarer).
- **Haushalt/Mehrgeraete (C5)**: kurz nachgeprueft -- nur native
  Symbolnamen (`TeamingUIServiceImpl`), keine fuer p2maps relevanten
  Kotlin-Modelle gefunden. Bleibt unveraendert offen, niedrige
  Prioritaet.
- **Housekeeping**: `py.typed`-Marker, GitHub-Actions-CI (Test-Matrix
  3.11-3.13 + ruff-Lint, hat einen echten ungenutzten Import gefunden),
  englisches nutzerseitiges README (Konvention: Englisch fuer GitHub-
  Inhalte) -- die vorherige deutsche Fassung liegt jetzt unter
  `docs/DEVELOPMENT_NOTES.md`.
- **Ader-Update-Entwurf**: `docs/ADER_UPDATE_DRAFT_2026-07-11.md` --
  fasst die drei wichtigsten Funde dieser Sitzung zusammen (Shadow-
  Transport fuer Kommandos, tar.gz-Kartenbuendel, Livemap-Fixed-Topic).

---
## Update (spaeter am selben Tag): was seitdem bearbeitet wurde

- **B1 (Livemap-Topic)**: umgebaut. `watch_live_map()` abonniert jetzt
  sofort ein festes Topic (`mqtt.livemap_topic()`), `get_live_map_stream()`
  laeuft als periodischer Hintergrund-Keep-Alive weiter. Braucht
  `irbt_topic_prefix` aus `LoginResult` (neues, unsicheres Feld --
  Discovery-JSON-Feldname geraten, nicht bestaetigt) -- fehlt der,
  wirft `watch_live_map()` sofort einen klaren `RuntimeError` statt
  still auf ein falsches Topic zu warten.
- **B2 (Furniture-Felder)**: ZURUECKGEZOGEN, war ein Fehler meinerseits.
  Ich hatte das Lese-Modell (P2MapFurnitureInfo) mit dem Editier-
  Kommando verglichen. Die tatsaechliche Edit-Klasse
  (EditMapV2Request.Furniture) hat wirklich nur 4 Felder -- kein
  Nachbesserungsbedarf am bestehenden `Furniture`-Editierkommando.
  `orientation`/`cleaning_area` gehoeren korrekt ins neue Lese-Modell
  `FurnitureInfoRead` (siehe C3).
- **C2 (fehlende Fetch-Endpunkte)**: `fetchActiveVersions` jetzt
  bestaetigt und implementiert (`get_active_map_versions()` ->
  `GET /v1/p2maps?robotId={blid}&visible=true`) -- die INNERE
  Coroutine-Klasse (P2MapAPIFetching$fetchActiveVersions$2) dekompilierte
  sauber, obwohl die aeussere Wrapper-Methode das nicht tat. Die
  anderen drei (fetchPersistentMap/fetchLatestPersistentMap/
  fetchMissionMap) bleiben unbestaetigt, aber mit neuem Kontext: das
  Kartenbuendel ist ein **tar.gz-Archiv**, kein JSON -- heruntergeladen
  von einer vorsignierten URL (`P2MapAPI.MapUnpacker.
  fetchMapBundleContentHolder(mapId, mapVersion)` loest die URL auf,
  bleibt "nicht decompiled"; eine zweite Methode mit derselben Signatur
  aber direktem URL-Parameter zeigt dann nur noch "Download + Untar",
  keine URL-Konstruktion mehr).
- **C3 (Lese-Modelle)**: grosser Batch neuer Dataclasses in `models.py`
  ergaenzt (RoomInfo, BorderInfo, TrajectoryInfo, CoverageInfo,
  DockInfo, HazardInfo, NoMopZoneInfo, AdHocCleanZoneInfo,
  KeepOutZoneInfoRead, VirtualWallInfo, CleanZoneInfoRead,
  FurnitureInfoRead) -- aber weiterhin KEIN Parser, der eine komplette
  Antwort in diese Typen zerlegt (das Gesamt-Umschlagformat, jetzt als
  tar.gz-Archiv bestaetigt, wurde nicht weiter untersucht).
- **C1 (Missionssteuerung), C4 (Auth-Mechanismus), C5 (Haushalt)**:
  unveraendert offen, siehe unten.

---

Systematischer Abgleich der heute (und in vorherigen Sitzungen)
dekompilierten Prime-App-Quellen (`roomba_prime_decompiled.zip`,
`roomba_prime_native_libs.zip`) gegen den tatsächlichen Bibliotheks-Code.
Drei Fundkategorien: **(A)** implementiert und im Kern korrekt,
**(B)** implementiert, aber mit einem konkreten Design-Fehler, **(C)**
gar nicht implementiert — mit Unterscheidung, ob das ein schließbares
Wissens-Loch ist oder eine echte native Grenze.

---

## A. Implementiert und im Kern korrekt

- **Login-Fluss** (Discovery → Gigya → iRobot `/v2/login`) — Feldnamen,
  Header, Payload-Form 1:1 bestätigt, sowohl gegen echte Classic-
  Fixtures als auch gegen `ha_roomba_plus`s produktiven Code.
- **AWS-IoT-Custom-Authorizer-Verbindung** (WebSocket, drei Auth-Header,
  Shadow-Get/-Update) — live gegen echte Classic-Geräte getestet.
- **p2maps-Editierbefehle** (`POST /v2/p2maps/{id}/versions`, 10
  Kommandotypen) — auf Java-Quellcode-Ebene vollständig bestätigt.
- **Kontinuierliche Dispatch-Schleifen, Token-Refresh, Backpressure** —
  eigene Architekturentscheidungen, nicht aus der App übernommen, aber
  in sich konsistent.

---

## B. Implementiert, aber mit einem konkreten Design-Fehler

### B1. watch_live_map() / get_live_map_stream() — falsches Modell

**Was ich gebaut habe:** REST-Aufruf liefert ein `mqtt_topic`-Feld,
das dann abonniert wird.

**Was die App tatsächlich tut** (`P2MapAPIFetching.observeLiveMap()`,
heute erstmals im Detail gelesen):

1. Abonniert sofort ein festes Topic-Muster:
   `mqttClient.subscribe(MQTTTopicPrefixType.irbt, "livemap/update", assetId)`
   aufgelöst über `MQTTTopicResolverAdapter` zu
   `{irbtTopicPrefix}/{identifier}` (exakte Verkettung von assetId und
   "livemap/update" zu identifier nicht letztgültig bestätigt, aber
   das Muster "fest, nicht aus der REST-Antwort" ist eindeutig).
2. Der REST-Aufruf `GET /v1/p2maps/livemap` (mein get_live_map_stream())
   ist in Wirklichkeit ein periodischer Keep-Alive-Ping
   (`LiveMapKeepAlivePublisher`, Timer via refreshWindowMillis) — die
   Antwort wird nirgends zur Topic-Ermittlung verwendet. Gezielt
   geprüft: LiveMapStreamResponse.topic (das mqtt_topic-Feld) wird im
   gesamten App-Code kein einziges Mal gelesen — nur beim Parsen
   erzeugt, nie konsumiert.

**Konsequenz:** get_live_map_stream() ist als REST-Aufruf vermutlich
richtig (deckt sich mit LiveMapStreamRequest), aber sein Zweck ist
falsch verstanden — es ist ein Keep-Alive, kein "gib mir das Topic"-
Aufruf. watch_live_map() müsste stattdessen sofort auf ein festes
Topic abonnieren UND parallel periodisch den Keep-Alive-Ping senden,
solange der Watcher läuft.

**Neuer, echter Lückenteil:** irbtTopicPrefix/iotTopicPrefix fehlen
komplett in auth.py's Discovery-Parsing — ohne die kann das feste
Topic gar nicht zusammengesetzt werden. Exakter JSON-Feldname in der
Discovery-Antwort nicht bestätigt (ServiceDiscoveryData ist eine
native/JNI-Klasse, Feldnamen dort sind C++-Konvention, nicht
zwangsläufig identisch mit dem Wire-JSON-Key).

**Empfehlung:** Nicht sofort umbauen — das ist eine grundlegende
Architekturänderung an bereits getestetem Code. Erst klären: (a)
exakter Discovery-Feldname für die Topic-Prefixes, (b) exakte
Verkettungsreihenfolge in identifier. Bis dahin: watch_live_map()s
Docstring um diesen Vorbehalt ergänzen.

### B2. Furniture-Editierbefehl — zwei Felder fehlen

Das reale Lesemodell P2MapFurnitureInfo hat cleaningArea: Polygon und
orientation: double zusätzlich zu geometry/id/type/userEdited. Meine
Furniture-Dataclass (für set_furniture) hat nur furniture_type,
geometry, furniture_id, user_modified — kein cleaning_area, kein
orientation. Wahrscheinlich Pflichtfelder beim Erstellen/Ändern von
Möbeln, nicht nur beim Lesen.

---

## C. Nicht implementiert

### C1. Missionssteuerung (CLEAN/START/STOP/PAUSE/DOCK/etc.) — größte Lücke, aber strukturell hart

Vollständiges Kommando-Vokabular gefunden
(com.irobot.data.missioncommand.datamodels.CommandType, 30 Werte):
CLEAN, QUICK, SPOT, DOCK, START, PAUSE, RESUME, STOP, WAKE, RESET,
FIND, WIPE, IPDONE, PROVDONE, RECHRG, TRAIN, EVAC, STOPEVAC, QUERYDOCK,
TIDY, VIEWPOINT, STARTLOG, SKIP, FLREFILL, WASHPAD, DRYPAD, STOPPADDRY,
FLUSHSLUICE, CLEAN_SPOT, START_CLEAN. RoutineCommand-Struktur (type,
assetId, mapId, ordered, idMultipolys, params, regions, pmapVersionId,
cleanAll, spotGeometry, favoriteId) ebenfalls bestätigt.

**Warum das (noch) nicht baubar ist:** MissionRepositoryImpl (der
Kotlin-Code, der startMission() aufruft) delegiert an
MissionInitiation/ProductStatus/core::CommandTierAgentImpl::
postCommand() — allesamt native JNI-Wrapper-Klassen. Die eigentliche
Übertragung (MQTT-Topic? Shadow-desired-Zustand? REST?) passiert in
liblegacyCore.so, für Java/Kotlin-Analyse unsichtbar. Das deckt sich
mit der schon in CLOUD_SHADOW_PUSH_FINDINGS.md festgehaltenen offenen
Frage ("kein Kommando-Topic in der APK gefunden... vermutlich über den
Shadow-desired-Zustand, nie getestet") — heute erneut bestätigt, nicht
aufgelöst.

**Das ist die fundamentalste fehlende Funktion der Bibliothek** — ohne
sie kann man den Roboter nicht starten/stoppen. Aber: kein Wissens-Loch,
das durch mehr Kotlin-Lesen zu schließen wäre. Nächster Schritt wäre
entweder native Disassemblierung (über Symbolnamen hinaus) oder eine
echte Traffic-Capture gegen ein Prime-Gerät.

### C2. p2maps-Lese-Endpunkte — Stand nach vierter Sitzung

P2MapFetching-Interface hat sechs Methoden:

| Methode | Status |
|---|---|
| fetchMapMetadata | erledigt: `get_map_metadata()` |
| fetchActiveVersions | erledigt: `get_active_map_versions()` |
| observeLiveMap | teilweise: `watch_live_map()`, aber siehe B1 |
| fetchPersistentMap / fetchLatestPersistentMap / fetchMissionMap | Endpunkt fuer den vorsignierten Download-Link jetzt bestaetigt (`get_map_geojson_link()`, siehe vierte Sitzung oben) -- das eigentliche Herunterladen+Entpacken des tar.gz-Buendels von dieser URL ist NICHT implementiert (waere ein einfacher HTTP-GET + tarfile-Entpacken, aber noch nicht gebaut) |

**Zusaetzlich gefunden, nicht implementiert:** `delete_map()` (klein,
implementiert) und die parallele V1-Editier-Vokabular (gross, bewusst
nicht implementiert -- siehe vierte Sitzung oben).

Fruehere Einschaetzung ("Method not decompiled", "nicht wirtschaftlich
weiter aufloesbar") war zu pessimistisch -- eine breitere Suche im
bereits vorhandenen Quellcode (nicht erneute Dekompilierung) loeste
den Kern der Frage.

### C3. p2maps-Lese-Modelle (was IN einer Karte steckt) — komplett fehlend

models.py hat ausschließlich Editier-Kommando-Hüllen (was man SENDET).
Für das, was fetchPersistentMap/fetchMissionMap/get_map_metadata
tatsächlich ZURÜCKLIEFERN, existiert kein einziges Datenmodell.
Bestätigt vorhandene, aber nicht abgebildete Lese-Typen: P2MapRoomInfo,
P2MapBorderInfo, P2MapHazardInfo, P2MapTrajectoryInfo,
P2MapCoverageInfo, P2MapDockInfo, P2MapFloorPlanInfo,
P2MapNoMopZoneInfo, P2MapAdHocCleanZoneInfo, P2MapKeepOutZoneInfo,
P2MapVirtualWallInfo, P2MapThresholdInfo, P2MapFurnitureInfo
(Lese-Variante, siehe B2), P2MapRoomMetadata. get_map_metadata() gibt
aktuell rohes, ungeparstes JSON zurück — ehrlich dokumentiert
("Response shape not modeled yet"), aber eine große Lücke.

### C4. Auth-Mechanismus für p2maps — heute als strukturell unbestätigbar bestätigt

AuthHTTPClientAdapter.perform() (der reale HTTP-Client-Pfad der
Prime-App) delegiert die gesamte Anfrage — inklusive Signierung — an
accountService.sendRequest(), wieder eine native Methode. Die
SigV4-Annahme in rest_client.py/aws_sigv4.py stammt aus der
Cross-Referenz mit ha_roomba_plus's cloud_api.py (Classic-Protokoll,
eigene Reverse-Engineering-Quelle) — sie war und bleibt eine
Analogie-Annahme, keine aus Primes eigenem Code bestätigte Tatsache.
Heute neu: der Grund, warum sie nie aus Primes eigenem Code bestätigt
werden kann, ist jetzt klar (native Delegation), nicht nur "nicht
geprüft".

### C5. Haushalt/Mehrgeräte-Konzepte — nicht untersucht

teaming/capability_profiles wurden in früheren Sitzungen als Konzepte
erwähnt (geteilter nativer Kern), heute nicht vertieft. Echte Lücke,
aber niedrige Priorität ohne mehrere Testgeräte im Haushalt.

---

## Priorisierter Vorschlag, nicht verbindlich

1. **B1 (Livemap-Topic-Fix)** — betrifft eine bereits gebaute, getestete
   Funktion; sollte vor allem Neuen korrigiert werden, sobald der
   Discovery-Feldname für irbtTopicPrefix geklärt ist.
2. **B2 (Furniture-Felder)** — klein, schnell zu ergänzen.
3. **C3 (Lese-Modelle)** — großer, aber gut abgrenzbarer Batch, direkt
   aus den heute gefundenen Klassennamen ableitbar.
4. **C2 (fehlende Fetch-Endpunkte)** — braucht zuerst einen erneuten,
   gezielten Dekompilierungsversuch für die vier "nicht decompiled"
   Methoden.
5. **C1 (Missionssteuerung)** — wichtigstes Feature, aber am schwersten
   zu schließen. Realistischer nächster Schritt: gezielte native
   Disassemblierung von CommandTierAgentImpl::postCommand() oder
   Warten auf echte Traffic-Daten (Ader).

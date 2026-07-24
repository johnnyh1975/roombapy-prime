# roombapy-prime — Write-Path Test Status (systematisch)

> Stand: v0.1.11a21 / Roomba+ v4.0.0a6. Konsolidiert aus allen bisherigen Feldtests,
> damit nichts doppelt gefragt oder übersehen wird.

## Legende

✅ live bestätigt · ⚠️ teilweise/mit Vorbehalt · ❌ nie getestet · 🚫 zentral blockiert

---

## 1. `verify-schedule-write` (`update_schedules()`)

| Schritt | Status | Von |
|---|---|---|
| Resend unverändert | ✅ | chairstacker |
| Zeitplan deaktivieren | ✅ | chairstacker |

**Fertig.** Kein weiterer Testbedarf.

---

## 2. `verify-map-edit` (`edit_map()` / Raumumbenennung)

| Schritt | Status | Von |
|---|---|---|
| Raum umbenennen | ✅ | chairstacker (2×) |

**Fertig.** Kein weiterer Testbedarf.

---

## 3. `verify-favorite-write` (`create_favorite()`/`update_favorite()`/`delete_favorite()`)

| Stufe | Status | Von | Anmerkung |
|---|---|---|---|
| 0 — Liste | ✅ | chairstacker | |
| 1 — Resend unverändert | ✅ | chairstacker | |
| 2 — Farbe ändern | ✅ | chairstacker | |
| 3 — Erstellen+Löschen | ⚠️ | chairstacker | Erstellt/gelöscht bestätigt, aber **App-Sichtbarkeit ungeklärt** — offene Rückfrage steht noch aus |
| Eigenständiges `--delete` | ✅ | chairstacker | Direkt bestätigt (der 409-Kollisions-Fall) |

**Fast fertig.** Einzig offen: Klärung mit chairstacker, ob "gelöscht" auf App-Ebene oder nur API-Ebene geprüft wurde (siehe letzte Nachricht an ihn).

---

## 4. `verify-region-commands` (`send_routine_command_via_cmd_topic()`) 🚫 zentraler Blocker

| Stufe | Status | Von | Anmerkung |
|---|---|---|---|
| 0 — Favoriten listen | ✅ | chairstacker, jayjay13011 | |
| 1 — Resend unverändert | ✅ negativ (vor Fix) | chairstacker, jayjay13011 | Beide: keine Wirkung — aber `favorite_id` fehlte im gesendeten Payload, siehe unten |
| 1b — mit `initiator` | ⏳ ausstehend | jayjay13011 (a19, korrekter `rmtApp`-Wert) | Ergebnis noch nicht zurückgemeldet — **die wichtigste offene Einzelfrage** |
| 2 — Suction-Level ändern | ✅ negativ (ohne initiator/favorite_id) | jayjay13011 | Lief technisch fehlerfrei, aber ohne beide jetzt bekannten fehlenden Felder |
| 3 — Eigener Raum, kein Favorit | ✅ negativ (ohne initiator) | jayjay13011 | Echte Raumnamen erstmals bestätigt (`--list-rooms`) |
| 4 — Ad-hoc/TID-Zone | ❌ | — | Höchstes Risiko, braucht selbst ermittelte Geometriedaten |

**Zwei reale Codelücken in dieser Session gefunden, beide behoben:**
- **a20**: Stufe 2/3 haben `initiator` nie gesetzt (nur Stufe 1b) — jeder bisherige Stufe-2/3-Test hat die eigentliche Hypothese nie geprüft
- **a21, größerer Fund**: **`favorite_id` wurde in keiner Stufe (1/1b/2) je gesetzt**, obwohl die eigene Recherche (`send_routine_command_via_cmd_topic()`s Docstring) längst bestätigt, dass die echte App es beim Wiederholen eines Favoriten immer mitschickt. Betrifft **rückwirkend alle bisherigen negativen Ergebnisse** — keiner der bisherigen Tests hat je ein wirklich app-äquivalentes Kommando gesendet.

**Der Engpass bleibt Stufe 1b/2 mit dem jetzt vollständigen Payload** (a21) — noch nicht erneut getestet. Alles bisher Beobachtete (inkl. jayjays Stufe 1b auf a19) ist damit vorläufig überholt, sobald jemand auf a21 aktualisiert.

---

## 5. `roombapy-prime-verify-region-commands-session` (neu, a19+)

Session-Runner für Stufe 1→1b→2 mit einem Login, automatischer Favoriten-Auswahl, strukturierter
Event-Zusammenfassung. Reduziert Wiederholungsaufwand, ersetzt aber nicht die eigentlichen
Testergebnisse — noch niemand hat ihn mit dem vollständigen (a21) Payload durchlaufen.

---

## 6. `verify-virtual-wall-write` (`SetVirtualWallsV1`)

| Stufe | Status | Von |
|---|---|---|
| 0 — Liste | ❌ | — |
| 1 — Resend unverändert | ❌ | — |

**Komplett unangefasst** — die einzige der vier "normalen" Schreibskripte, die noch niemand auch nur einmal ausprobiert hat.

---

## 7. `verify-settings-write` (`set_setting()` für 5 Settings)

| Setting | Status |
|---|---|
| `child_lock` | ❌ |
| `eco_charge` | ❌ |
| `sched_hold` | ❌ (inkl. offener Frage: synct sich mit der zweiten `schedHold`-Quelle in der classic/unnamed-Shadow?) |
| `no_auto_passes` | ❌ |
| `vac_high` | ❌ |

**Komplett unangefasst** — Skript existiert erst seit a19, noch nie kommuniziert.

---

## Priorisierter Plan für die nächste Testrunde

1. **Region-Commands Stufe 1b/2 auf a21 erneut testen** — mit `favorite_id` **und** `initiator`
   jetzt erstmals vollständig — höchste Priorität, überholt alle bisherigen Ergebnisse
2. **Favoriten-Rückfrage an chairstacker** — kurze Klärung (App- vs. API-Sichtbarkeit), kein neuer Test
3. **Virtual-Wall-Write Stufe 0/1** — niedrigstes Risiko unter den komplett unangefassten Skripten
4. **Settings-Write** — danach, inkl. der `schedHold`-Doppelquellen-Frage
5. **Region-Commands Stufe 3** erneut mit `initiator` (a20-Fix) — jayjay13011 hat bereits echte
   Raumdaten und Erfahrung damit
6. **Region-Commands Stufe 4** — erst, wenn 1b/2 auf a21 ein klares Ergebnis liefern

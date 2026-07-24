# roombapy-prime — Write-Path Test Status (systematisch)

> Stand: v0.1.11a20 / Roomba+ v4.0.0a6. Konsolidiert aus allen bisherigen Feldtests,
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
| 1 — Resend unverändert | ✅ negativ | chairstacker, jayjay13011 | Beide: keine Wirkung |
| 1b — mit `initiator` | ❌ | — | War mit falschem Wert (`localApp`) angefragt, nie mit dem korrekten (`rmtApp`, seit a19) abgeschlossen — **die wichtigste offene Einzelfrage im ganzen Projekt** |
| 2 — Suction-Level ändern | ❌ | — | jayjay13011 traf einen echten Crash (behoben in a17), nie erneut versucht |
| 3 — Eigener Raum, kein Favorit | ❌ | — | Höheres Risiko, noch niemand |
| 4 — Ad-hoc/TID-Zone | ❌ | — | Höchstes Risiko, braucht selbst ermittelte Geometriedaten |

**Der Engpass.** Stufe 1b ist gerade als eigene, konsolidierte Anfrage raus (an alle vier).

---

## 5. `verify-virtual-wall-write` (`SetVirtualWallsV1`)

| Stufe | Status | Von |
|---|---|---|
| 0 — Liste | ❌ | — |
| 1 — Resend unverändert | ❌ | — |

**Komplett unangefasst** — die einzige der vier "normalen" Schreibskripte, die noch niemand auch nur einmal ausprobiert hat.

---

## 6. `verify-settings-write` (`set_setting()` für 5 Settings)

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

1. **Region-Commands Stufe 1b** — bereits raus, höchste Priorität, wartet auf Rückmeldung
2. **Favoriten-Rückfrage an chairstacker** — kurze Klärung, kein neuer Test
3. **Virtual-Wall-Write Stufe 0/1** — niedrigstes Risiko unter den komplett unangefassten Skripten, nächstes Issue
4. **Settings-Write** — danach, inkl. der `schedHold`-Doppelquellen-Frage
5. **Region-Commands Stufe 2** (mit dem a17-Fix) — kann parallel zu 1b angefragt werden, an jayjay13011 (der den Crash hatte)
6. **Region-Commands Stufe 3/4** — erst, wenn 1b ein klares Ergebnis liefert (bei "funktioniert nicht", ändert sich vermutlich der ganze Ansatz für 3/4 mit)

# Fixtures

Anonymisierte, aber strukturell echte Rohantworten aus Classic-Protokoll-
Tests (EPHEMERAL 980, SMART-Tier i7 x2). Herkunft je Datei:

- `login_response_ephemeral.json` / `login_response_smart_tier.json` —
  /v2/login-Antworten, BLID/Tokens ersetzt, echte Feldnamen und
  Capability-Werte erhalten (insb. cap.pose: 1 vs. 2)
- `shadow_get_classic_ephemeral.json` — vollständiger klassischer Shadow
  (980)
- `shadow_get_classic_smart_tier.json` — klassischer Shadow (i7),
  Feldtester-Capture war bei "digiCap" abgeschnitten
- `shadow_get_rw_settings_smart_tier.json` — benannter Shadow (i7),
  Feldtester-Capture war bei "langs2" abgeschnitten
- `shadow_update_accepted.json` — echte No-op-Schreib-Antwort (980)

Classic-Cloud-REST (0.4.0), übernommen aus `ha_roomba_plus/tests/fixtures/`,
dort als echte Aufzeichnungen eines i3+ geführt (sku i355640, daredevil+2.6.0):

- `classic_pmaps_i3plus.json` — Antwort von `GET /v1/{blid}/pmaps`
  (`visible=true`, `activeDetails=2`), eine Karte
- `classic_missionhistory_i3plus.json` — Antwort von
  `GET /v1/{blid}/missionhistory` mit den Classic-Parametern: eine **Liste**
  von drei Missionen, kein Objekt
- `classic_parts_i3plus.json` — Antwort von `GET /v1/robots/{blid}/parts`,
  vier Teile

Das UMF-Fixture der Integration (`irobot_mission_umf_i3plus.json`) ist
bewusst nicht übernommen: Es stammt aus einer Missionskarte, und dass es die
Antwort von `…/pmaps/{id}/versions/{v}/umf` ist, ist nicht belegt.

Kein V4/Prime-Fixture vorhanden — bewusst, da keines existiert. Siehe
`tests/test_auth.py`/`tests/test_mqtt_client.py` für die Verwendung;
synthetische (nicht aus echten Captures stammende) Testfälle sind dort
explizit als SYNTHETIC markiert.

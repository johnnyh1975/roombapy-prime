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

Kein V4/Prime-Fixture vorhanden — bewusst, da keines existiert. Siehe
`tests/test_auth.py`/`tests/test_mqtt_client.py` für die Verwendung;
synthetische (nicht aus echten Captures stammende) Testfälle sind dort
explizit als SYNTHETIC markiert.

"""roombapy-prime — Cloud client for iRobot "Prime"/V4-generation robots.

STATUS (11. Juli 2026, zwoelfte Sitzung): Draft, vollstaendig durch
statische Analyse (Kotlin/Java-Dekompilierung + native Bytecode-
Inspektion) entstanden. Umfangreiche Funktionsabdeckung (Auth, MQTT-
Shadow, Missionssteuerung, p2maps-Kartenbearbeitung, Favoriten,
Zeitplaene, DND, Reinigungsprofile, Missionshistorie) -- aber NIE gegen
einen echten Server oder ein echtes V4-Geraet getestet. Siehe
docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md fuer den vollstaendigen,
laufend aktualisierten Auditstand und README.md fuer den
Contributing-Abschnitt (roombapy_prime.diagnostics -- das
Live-Validierungsskript, mit dem sich das aendern liesse).

Warum eine eigene Bibliothek statt einer Erweiterung von `roombapy`:

`roombapy`'s RoombaRemoteClient setzt `ssl.CERT_NONE` global gecacht
(korrekt fuer lokale Verbindungen, unsicher fuer einen echten
Internet-Endpunkt) und erwartet (address, blid, password) als lokale
IP -- strukturell inkompatibel mit einem Cloud-Client. Es ist kein
Anpassungsproblem, sondern ein grundlegend anderes Vertrauens- und
Verbindungsmodell. Siehe docs/ROOMBAPY_COMPARISON.md fuer den
vollstaendigen Vergleich.

Der Name "Prime" ist iRobots eigene Bezeichnung (com.irobot.home.prime
App), nicht unser informelles "V4".

Modulstruktur:
    auth.py           -- Gigya -> Custom-Authorizer-Token
    mqtt_client.py     -- AWS-IoT-WebSocket-Verbindung, echte Zertifikatspruefung
    rest_client.py     -- p2maps, Favoriten, Zeitplaene, DND, Missionshistorie, etc.
    models.py          -- State-/Kommando-Payload-Typen
    prime_robot.py     -- Oeffentliche Klasse (Analog zu roomba.py in roombapy)
    prime_factory.py   -- Factory: username/password/blid statt lokaler IP
    diagnostics.py     -- Live-Validierungsskript gegen einen echten Account
"""

__version__ = "0.1.0.dev0"

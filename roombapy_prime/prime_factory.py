"""Factory: username/password/blid statt lokaler IP (Cloud-Client-Aufbau).

STATUS: Draft. Namenskonvention bewusst an roombapy.roomba_factory
angelehnt (RoombaFactory.create_roomba(...) -> PrimeFactory.
create_prime_robot(...)) fuer Wiedererkennbarkeit -- siehe
docs/ROOMBAPY_COMPARISON.md Abschnitt 4. Anders als roombapy's Factory
ist diese hier zwangslaeufig async, weil der Verbindungsaufbau einen
echten Login-Flow braucht (roombapy braucht dafuer nichts, da es direkt
mit einer lokalen IP + bereits bekanntem Passwort arbeitet).

NICHT gegen ein echtes V4-Konto getestet -- reine Verdrahtung von
bereits einzeln dokumentierten Bausteinen (auth.py, mqtt_client.py,
rest_client.py, prime_robot.py).

Seit heute: auto_refresh-Parameter (siehe unten) fuer proaktiven
Token-Refresh -- siehe prime_robot.py's Modul-Docstring fuer den
Zugangsdaten-im-Speicher-Tradeoff, den das mit sich bringt.
"""
from __future__ import annotations

import aiohttp

from .auth import login
from .mqtt_client import PrimeMqttClient
from .prime_robot import PrimeRobot
from .rest_client import PrimeRestClient


class PrimeFactory:
    """Analog zu roombapy.RoombaFactory, aber async und Account-basiert
    statt lokaler-IP-basiert."""

    @staticmethod
    async def create_prime_robot(
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        country_code: str,
        blid: str | None = None,
        *,
        auto_refresh: bool = False,
    ) -> PrimeRobot:
        """Loggt ein, waehlt den Roboter aus (erster gefundener, falls
        blid nicht angegeben), verdrahtet MQTT- und REST-Client, gibt
        eine noch NICHT verbundene PrimeRobot-Instanz zurueck -- der
        Aufrufer muss selbst noch await robot.connect() aufrufen.

        auto_refresh=True: haelt username/password im Schliessungsbereich
        eines relogin-Callbacks, der zweifach genutzt wird -- proaktiv
        von PrimeRobot kurz vor MQTT-Token-Ablauf (siehe
        prime_robot.py's Modul-Docstring fuer den damit verbundenen
        Zugangsdaten-im-Speicher-Tradeoff), UND reaktiv von
        PrimeRestClient bei einem HTTP-403 auf einen p2maps-Aufruf
        (siehe rest_client.py). Default False -- bisheriges Verhalten
        (Zugangsdaten laufen nach ~1h ab, keine Ueberraschung fuer
        bestehende Aufrufer dieser Methode."""
        login_result = await login(session, username, password, country_code)
        target_blid = blid or login_result.primary_blid()
        token = login_result.token_for_blid(target_blid)

        mqtt_client = PrimeMqttClient(
            token=token,
            endpoint=login_result.mqtt_endpoint,
            blid=target_blid,
        )

        relogin = None
        if auto_refresh:

            async def relogin():
                return await login(session, username, password, country_code)

        rest_client = PrimeRestClient(
            session=session,
            http_base_auth=login_result.http_base_auth,
            credentials=login_result.credentials,
            relogin=relogin,
        )

        return PrimeRobot(
            blid=target_blid,
            mqtt_client=mqtt_client,
            rest_client=rest_client,
            relogin=relogin,
            irbt_topic_prefix=login_result.irbt_topic_prefix,
        )

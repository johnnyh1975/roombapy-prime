"""Deployment renewal through real account/REST/MQTT paths over fake I/O.

Event barriers control login/application order; no network or robot is used.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from roombapy_prime import account as account_module
from roombapy_prime.account import CloudAccount
from roombapy_prime.auth import CloudCredentials, ConnectionToken, LoginResult, RobotLoginEntry
from roombapy_prime.mqtt_client import PrimeMqttClient
from roombapy_prime.tests.test_account import _Session
from roombapy_prime.tests.test_watch_reconnect import _Broker, _Watch, _until

A = '0123456789ABCDEF'
B = 'FEDCBA9876543210'


def snapshot(n, shared=True):
    depl = ('v007', 'v011', 'v022')[n]
    groups = [[A, B]] if shared else [[A], [B]]
    return LoginResult(
        mqtt_endpoint='mqtt.example.invalid',
        http_base='https://base.example.invalid',
        http_base_auth='https://auth.example.invalid',
        credentials=CloudCredentials(access_key_id='A', secret_key='S',
            session_token=f'session-L{n}', cognito_id='us-east-1:0'),
        robots={blid: RobotLoginEntry(sku='W155020', svc_deployment_id=depl) for blid in [A, B]},
        connection_tokens=[ConnectionToken(
            client_id=f'client-{i}', iot_token=f'snapshot-L{n}',
            iot_signature='synthetic', iot_authorizer_name='synthetic',
            expires=None if n == 0 else int(time.time()) + 3600,
            devices=devices) for i, devices in enumerate(groups)],
        raw={}, deployment={'snapshot': f'L{n}'}, irbt_topic_prefix='v007-irbthbu',
        deployments={d: {'irbtTopics': f'{d}-irbthbu'} for d in ['v007', 'v011', 'v022']},
    )


async def wait(event):
    await asyncio.wait_for(event.wait(), 3)


class Rig:
    def __init__(self, monkeypatch, shared=True):
        self.broker = _Broker()
        monkeypatch.setattr(PrimeMqttClient, '_build_client',
                            lambda _client, timeout: self.broker.build(timeout))
        self.snapshots = [snapshot(i, shared) for i in range(3)]
        self.session = _Session(expired={'session-L1'})
        self.account = CloudAccount(self.session, 'user@example.com', 'synthetic', 'DE', self.snapshots[0])
        self.trace = []
        self.applied = {}
        self.queued = {}
        self.tasks = []
        self.robots = []
        results = iter(self.snapshots[1:])

        async def authenticate(*_args, **_kw):
            result = next(results)
            self.trace.append(['obtained', result.deployment['snapshot']])
            return result

        self.login = AsyncMock(side_effect=authenticate)
        monkeypatch.setattr(account_module, 'login', self.login)
        original = PrimeMqttClient.replace_token

        async def observe(client, token, *args, **kwargs):
            label = (client._blid, token.iot_token)
            self.trace.append(['queued', *label])
            self.queued.setdefault(label, asyncio.Event()).set()
            await original(client, token, *args, **kwargs)
            self.trace.append(['applied', *label])
            self.applied.setdefault(label, asyncio.Event()).set()

        monkeypatch.setattr(PrimeMqttClient, 'replace_token', observe)

    async def add(self, blid):
        robot = await self.account.prime_robot(blid, auto_refresh=True)
        self.robots.append(robot)
        await robot.connect()
        if robot._mqtt.connection._token.expires is None and robot._refresh_task is not None:
            await robot._refresh_task
        return robot

    def expire(self):
        current = self.account.login_result
        due = replace(current, connection_tokens=[replace(t, expires=int(time.time()) - 1)
                                                  for t in current.connection_tokens])
        self.account._login_result = due
        for connection in self.account._connections:
            connection._token = due.token_for_blid(connection._blid)

    async def hold_shadow(self, robot):
        sent = asyncio.Event()
        answer = {}

        def published(conn, topic, _payload):
            if topic.endswith('/shadow/get'):
                answer.update(conn=conn, topic=topic + '/accepted')
                sent.set()

        self.broker.on_publish = published
        task = asyncio.create_task(robot._mqtt.get_shadow(timeout=5))
        self.tasks.append(task)
        await wait(sent)
        assert robot._mqtt.connection._lock.locked()

        async def release():
            answer['conn'].deliver(answer['topic'], b'{"state":{"reported":{}}}')
            await task

        return release

    def refresh(self, robot):
        task = asyncio.create_task(robot._refresh_loop())
        self.tasks.append(task)
        return task

    async def queued_login(self, robot, n=1):
        label = (robot._mqtt.connection._blid, f'snapshot-L{n}')
        await wait(self.queued.setdefault(label, asyncio.Event()))

    async def applied_login(self, robot, n=1):
        label = (robot._mqtt.connection._blid, f'snapshot-L{n}')
        await wait(self.applied.setdefault(label, asyncio.Event()))

    async def newer_login_through_rest(self):
        consumer = self.account.prime_rest()  # credentials from L1, not the old L0 client
        assert consumer._credentials is self.snapshots[1].credentials
        await consumer.get_robot_parts_raw(A)  # fake GET -> 403 -> real account refresh -> fake GET
        assert self.account.login_result is self.snapshots[2]
        assert self.session.tokens_seen[-2:] == ['session-L1', 'session-L2']
        self.trace.append(['rest-consumed', 'L2'])

    def no_mission_commands(self):
        assert not any(topic.endswith('/cmd') for conn in self.broker.conns for topic, _ in conn.published)

    async def close(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for robot in reversed(self.robots):
            await robot.disconnect()


@pytest.mark.asyncio
async def test_shared_renewals_do_not_reverse_commits_even_if_rest_gets_a_newer_login(monkeypatch):
    rig = Rig(monkeypatch)
    try:
        first, second = await rig.add(A), await rig.add(B)
        rig.expire()
        release = await rig.hold_shadow(first)
        rig.refresh(first)
        await rig.queued_login(first, 1)
        await rig.newer_login_through_rest()
        share = first._share
        waiting = asyncio.Event()
        acquire = share.lock.acquire

        async def observed_acquire():
            if share.lock.locked():
                waiting.set()
            return await acquire()

        monkeypatch.setattr(share.lock, 'acquire', observed_acquire)
        rig.refresh(second)
        await wait(waiting)
        assert not any(e[0] == 'applied' for e in rig.trace)
        await release()
        await rig.applied_login(first, 1)
        assert [e for e in rig.trace if e[0] == 'applied'] == [['applied', A, 'snapshot-L1']]
        assert first._irbt_topic_prefix == second._irbt_topic_prefix == 'v011-irbthbu'
        assert first._mqtt.connection._token is rig.snapshots[1].token_for_blid(A)
        assert rig.account.login_result is rig.snapshots[2]
        assert rig.login.await_count == 2
        rig.no_mission_commands()
        print('shared:', json.dumps(rig.trace))
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_separate_connections_apply_global_snapshots_out_of_order_without_cross_rollback(monkeypatch):
    rig = Rig(monkeypatch, shared=False)
    try:
        first, second = await rig.add(A), await rig.add(B)
        rig.expire()
        release = await rig.hold_shadow(first)
        rig.refresh(first)
        await rig.queued_login(first, 1)
        await rig.newer_login_through_rest()
        rig.refresh(second)
        await rig.applied_login(second, 2)
        assert second._irbt_topic_prefix == 'v022-irbthbu'
        await release()
        await rig.applied_login(first, 1)
        assert [e for e in rig.trace if e[0] == 'applied'] == [
            ['applied', B, 'snapshot-L2'], ['applied', A, 'snapshot-L1']]
        assert first._irbt_topic_prefix == 'v011-irbthbu'
        assert second._irbt_topic_prefix == 'v022-irbthbu'
        assert first._mqtt.connection._token is rig.snapshots[1].token_for_blid(A)
        assert second._mqtt.connection._token is rig.snapshots[2].token_for_blid(B)
        assert rig.login.await_count == 2
        rig.no_mission_commands()
        print('separate:', json.dumps(rig.trace))
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_a_new_handle_must_not_lose_metadata_from_a_newer_account_login(monkeypatch):
    rig = Rig(monkeypatch)
    try:
        first = await rig.add(A)
        rig.expire()
        release = await rig.hold_shadow(first)
        rig.refresh(first)
        await rig.queued_login(first, 1)
        await rig.newer_login_through_rest()
        entered = asyncio.Event()

        async def construct():
            entered.set()
            second = await rig.account.prime_robot(B, auto_refresh=True)
            rig.robots.append(second)
            rig.trace.append(['constructed-B', second.svc_deployment_id])
            return second

        create = asyncio.create_task(construct())
        rig.tasks.append(create)
        await wait(entered)
        before = create.result()._irbt_topic_prefix if create.done() else 'construction waiting'
        await release()
        await rig.applied_login(first, 1)
        second = await create
        await second.connect()
        observation = {'before_L1_commit': before,
                       'after_L1_commit': second._irbt_topic_prefix,
                       'account_snapshot': rig.account.login_result.deployment['snapshot'],
                       'connection_token_snapshot': second._mqtt.connection._token.iot_token,
                       'trace': rig.trace}
        print('new-handle:', json.dumps(observation))
        assert rig.account.login_result is rig.snapshots[2]
        assert rig.login.await_count == 2
        rig.no_mission_commands()
        assert second._irbt_topic_prefix == 'v022-irbthbu'
        assert second.svc_deployment_id == 'v022'
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_an_old_rest_consumer_reuses_the_existing_login_instead_of_getting_L2(monkeypatch):
    rig = Rig(monkeypatch)
    try:
        first = await rig.add(A)
        old_consumer = rig.account.prime_rest()
        rig.session.expired = {'session-L0'}
        rig.expire()
        release = await rig.hold_shadow(first)
        rig.refresh(first)
        await rig.queued_login(first, 1)
        await old_consumer.get_robot_parts_raw(A)
        assert rig.session.tokens_seen[-2:] == ['session-L0', 'session-L1']
        assert rig.account.login_result is rig.snapshots[1]
        assert rig.login.await_count == 1
        await release()
        await rig.applied_login(first, 1)
        rig.no_mission_commands()
    finally:
        await rig.close()


async def renew(rig, robot):
    rig.expire()
    rig.refresh(robot)
    await rig.applied_login(robot, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['mission/timeline/report', '#'])
async def test_an_existing_raw_watcher_keeps_the_callers_literal_after_renewal(monkeypatch, suffix):
    rig = Rig(monkeypatch)
    watcher = None
    try:
        robot = await rig.add(A)
        old = f'v007-irbthbu/things/{A}/{suffix}'
        watcher = _Watch(robot.watch_raw_topic(old))
        client = robot._mqtt.connection
        await _until(lambda: old in client._persistent)
        await renew(rig, robot)
        print('raw-existing:', json.dumps({'literal': old, 'actual_topics': list(client._persistent)}))
        assert old in client._persistent
        assert old in rig.broker.live.subscribed
        topic = old if suffix != '#' else f'v007-irbthbu/things/{A}/diagnostic/report'
        rig.broker.live.deliver(topic, b'{"marker":"literal-old"}')
        await _until(lambda: len(watcher.got) == 1)
        rig.no_mission_commands()
    finally:
        if watcher:
            await watcher.stop()
        await rig.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['mission/timeline/report', '#'])
async def test_a_new_raw_watcher_can_subscribe_to_a_known_historical_literal(monkeypatch, suffix):
    rig = Rig(monkeypatch)
    watcher = None
    try:
        robot = await rig.add(A)
        await renew(rig, robot)
        old = f'v007-irbthbu/things/{A}/{suffix}'
        client = robot._mqtt.connection
        watcher = _Watch(robot.watch_raw_topic(old))
        await _until(lambda: bool(client._persistent))
        print('raw-new:', json.dumps({'literal': old, 'actual_topics': list(client._persistent)}))
        assert old in client._persistent
        assert old in rig.broker.live.subscribed
        rig.no_mission_commands()
    finally:
        if watcher:
            await watcher.stop()
        await rig.close()


@pytest.mark.asyncio
async def test_a_dedicated_timeline_watcher_follows_the_robot_deployment(monkeypatch):
    rig = Rig(monkeypatch)
    watcher = None
    try:
        robot = await rig.add(A)
        old = f'v007-irbthbu/things/{A}/mission/timeline/report'
        new = old.replace('v007', 'v011')
        client = robot._mqtt.connection
        watcher = _Watch(robot.watch_mission_timeline())
        await _until(lambda: old in client._persistent)
        await renew(rig, robot)
        assert old not in client._persistent and new in client._persistent
        rig.broker.live.deliver(new, b'{"marker":"managed-new"}')
        await _until(lambda: len(watcher.got) == 1)
        rig.no_mission_commands()
    finally:
        if watcher:
            await watcher.stop()
        await rig.close()


@pytest.mark.asyncio
async def test_raw_and_dedicated_watchers_can_keep_distinct_routing_policies(monkeypatch):
    rig = Rig(monkeypatch)
    watchers = []
    try:
        robot = await rig.add(A)
        old = f'v007-irbthbu/things/{A}/mission/timeline/report'
        new = old.replace('v007', 'v011')
        client = robot._mqtt.connection
        raw = _Watch(robot.watch_raw_topic(old))
        managed = _Watch(robot.watch_mission_timeline())
        watchers.extend([raw, managed])
        await _until(lambda: len(client._persistent.get(old, [])) == 2)
        await renew(rig, robot)
        print('mixed:', json.dumps({'actual_topics': list(client._persistent)}))
        assert old in client._persistent and new in client._persistent
        assert len(client._persistent[old]) == len(client._persistent[new]) == 1
        rig.broker.live.deliver(old, b'{"marker":"raw-old"}')
        rig.broker.live.deliver(new, b'{"marker":"managed-new"}')
        await _until(lambda: len(raw.got) == len(managed.got) == 1)
        assert raw.got == [{'marker': 'raw-old'}]
        assert managed.got == [{'marker': 'managed-new'}]
        rig.no_mission_commands()
    finally:
        for watcher in watchers:
            await watcher.stop()
        await rig.close()


@pytest.mark.asyncio
async def test_concurrent_handle_builds_wait_for_pending_login_and_cancel_without_registration(monkeypatch):
    rig = Rig(monkeypatch)
    try:
        first = await rig.add(A)
        rig.expire()
        release = await rig.hold_shadow(first)
        rig.refresh(first)
        await rig.queued_login(first, 1)
        await rig.newer_login_through_rest()
        client = first._mqtt.connection
        original_channels = list(client._channels)
        waiting = asyncio.Event()
        acquire = first._share.lock.acquire
        waiters = 0

        async def observed_acquire():
            nonlocal waiters
            if first._share.lock.locked():
                waiters += 1
                if waiters == 3:
                    waiting.set()
            return await acquire()

        monkeypatch.setattr(first._share.lock, 'acquire', observed_acquire)
        builds = [asyncio.create_task(rig.account.prime_robot(blid)) for blid in (A, B, B)]
        rig.tasks.extend(builds)
        await wait(waiting)
        assert client._channels == original_channels
        assert set(first._share.robots) == {first}
        builds[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await builds[1]
        assert client._channels == original_channels
        await release()
        await rig.applied_login(first, 1)
        created = await asyncio.gather(builds[0], builds[2])
        rig.robots.extend(created)
        assert all(r._irbt_topic_prefix == 'v022-irbthbu' for r in created)
        assert all(r._mqtt.connection is client for r in created)
        assert all(r._rest._credentials is rig.snapshots[2].credentials for r in created)
        assert set(first._share.robots) == {first, *created}
        assert len(client._channels) == 3
        assert len(rig.broker.conns) == 2  # pending renewal only; construction is offline
        assert rig.account.login_result is rig.snapshots[2]
        rig.no_mission_commands()
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_build_waiting_during_reconnect_backoff_is_cancellable_and_recovers(monkeypatch):
    rig = Rig(monkeypatch)
    watch = None
    release_backoff = asyncio.Event()
    try:
        first = await rig.add(A)
        client = first._mqtt.connection
        entered_backoff = asyncio.Event()
        original_wait = client.wait_for_state_change

        async def controlled_backoff(timeout):
            entered_backoff.set()
            await release_backoff.wait()
            return await original_wait(0)

        monkeypatch.setattr(client, 'wait_for_state_change', controlled_backoff)
        watch = _Watch(first.watch_state())
        topic = client.shadow_topic('update/delta')
        await _until(lambda: topic in client._persistent)
        rig.broker.connect_fail = 1
        rig.broker.live.drop()
        await wait(entered_backoff)
        assert first._share.lock.locked()
        original_channels = list(client._channels)
        waiting = asyncio.Event()
        acquire = first._share.lock.acquire

        async def observed_acquire():
            waiting.set()
            return await acquire()

        monkeypatch.setattr(first._share.lock, 'acquire', observed_acquire)
        build = asyncio.create_task(rig.account.prime_robot(B))
        rig.tasks.append(build)
        await wait(waiting)
        assert not build.done()
        build.cancel()
        with pytest.raises(asyncio.CancelledError):
            await build
        assert client._channels == original_channels
        assert set(first._share.robots) == {first}
        release_backoff.set()
        await _until(lambda: client.connected and not first._share.lock.locked())
        second = await rig.account.prime_robot(B)
        rig.robots.append(second)
        assert second._mqtt.connection is client
        assert len(rig.broker.conns) == 3  # original, failed, recovered
        rig.login.assert_not_awaited()
        rig.no_mission_commands()
    finally:
        release_backoff.set()
        if watch:
            await watch.stop()
        await rig.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_raw_and_dedicated_round_trip_preserves_callbacks_and_cleans_broker_topics(monkeypatch, failure):
    rig = Rig(monkeypatch)
    watchers = []
    try:
        robot = await rig.add(A)
        client = robot._mqtt.connection
        old = f'v007-irbthbu/things/{A}/mission/timeline/report'
        new = old.replace('v007', 'v011')
        aws = client.shadow_topic('update/delta')
        raw_old = _Watch(robot.watch_raw_topic(old))
        dedicated = _Watch(robot.watch_mission_timeline())
        shadow = _Watch(robot.watch_state())
        watchers.extend([raw_old, dedicated, shadow])
        await _until(lambda: len(client._persistent.get(old, [])) == 2 and aws in client._persistent)
        rig.broker.connect_fail = int(failure)
        if failure:
            from roombapy_prime.mqtt_client import ShadowError
            with pytest.raises(ShadowError):
                await robot._replace_login(rig.snapshots[1])
            assert not client.connected
            assert set(client._persistent) == {old, new, aws}
            await client.reconnect()
        else:
            await robot._replace_login(rig.snapshots[1])
        raw_new = _Watch(robot.watch_raw_topic(new))
        watchers.append(raw_new)
        await _until(lambda: len(client._persistent.get(new, [])) == 2)
        assert set(rig.broker.live.subscribed) == {old, new, aws}
        rig.broker.live.deliver(old, b'{"at":"old"}')
        rig.broker.live.deliver(new, b'{"at":"new"}')
        rig.broker.live.deliver(aws, b'{"at":"aws"}')
        await _until(lambda: all(len(w.got) == 1 for w in watchers))
        assert raw_old.got == [{'at': 'old'}]
        assert dedicated.got == raw_new.got == [{'at': 'new'}]
        await robot._replace_login(rig.snapshots[0])
        assert len(client._persistent[old]) == 2 and len(client._persistent[new]) == 1
        assert set(rig.broker.live.subscribed) == {old, new, aws}
        rig.broker.live.deliver(old, b'{"at":"returned"}')
        rig.broker.live.deliver(new, b'{"at":"literal-new"}')
        rig.broker.live.deliver(aws, b'{"at":"aws-returned"}')
        await _until(lambda: all(len(w.got) == 2 for w in watchers))
        assert raw_old.got[-1] == dedicated.got[-1] == {'at': 'returned'}
        assert raw_new.got[-1] == {'at': 'literal-new'}
        await dedicated.stop()
        assert len(client._persistent[old]) == 1
        assert rig.broker.live.unsubscribed == []
        for w in (raw_old, raw_new, shadow):
            await w.stop()
        watchers.clear()
        assert client._persistent == {}
        assert set(rig.broker.live.unsubscribed) == {old, new, aws}
        assert len(rig.broker.live.unsubscribed) == 3
        assert len(rig.broker.conns) == 3 + int(failure)
        rig.no_mission_commands()
    finally:
        for watcher in watchers:
            await watcher.stop()
        await rig.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['watch_mission_timeline', 'watch_dock_reports',
                                   'watch_rejected_commands', 'watch_live_map'])
async def test_each_dedicated_irbt_watcher_follows_and_unsubscribes_after_a_round_trip(monkeypatch, method):
    rig = Rig(monkeypatch)
    watch = None
    try:
        robot = await rig.add(A)
        # Live-map's existing REST keep-alive is isolated too; no HTTP request.
        monkeypatch.setattr(robot, 'get_live_map_stream', AsyncMock())
        client = robot._mqtt.connection
        watch = _Watch(getattr(robot, method)())
        await _until(lambda: len(client._persistent) == 1)
        old = next(iter(client._persistent))
        callback = client._persistent[old][0]
        new = old.replace('v007', 'v011')
        assert new != old
        await robot._replace_login(rig.snapshots[1])
        assert client._persistent == {new: [callback]}
        assert rig.broker.live.subscribed == [new]
        await robot._replace_login(rig.snapshots[0])
        assert client._persistent == {old: [callback]}
        assert rig.broker.live.subscribed == [old]
        await watch.stop()
        watch = None
        assert client._persistent == {}
        assert rig.broker.live.unsubscribed == [old]
        assert len(rig.broker.conns) == 3
        rig.no_mission_commands()
    finally:
        if watch:
            await watch.stop()
        await rig.close()

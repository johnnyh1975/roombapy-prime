Subject: roombapy-prime update — full draft implementation, several confirmed protocol details

Hi Ader,

Quick update on the V4/Prime library since we last talked. I decided
not to wait any longer for field data and built out a full draft
implementation from what's confirmable via static analysis alone —
APK decompilation, native library disassembly, and cross-referencing
against a separate, already-shipping Classic-protocol integration I
maintain. It's on GitHub now (link once I've pushed) with ~100 tests,
all passing against synthetic/decompiled-structure fixtures — but
NONE of it has touched a real V4 account yet, which is exactly where
I'd love your help if you're up for it.

A few things that might be useful for your own reverse-engineering
work, in case you hadn't gotten this far yet:

**Mission commands (start/stop/pause/dock/etc.) go through the shadow,
not a separate topic.** I found the literal format string
`$aws/things/%s/shadow/update` in liblegacyCore.so via aarch64 objdump
disassembly, and a matching `CommandWrapper` Kotlin class
(`@Serializable`, single field `cmd` with `@SerialName("cmd")`)
wrapping a `RoutineCommand`. So the shape should be:

    {"state": {"desired": {"cmd": {<RoutineCommand fields>}}}}

published to $aws/things/{id}/shadow/update. RoutineCommand's field
names are confirmed from @SerialName annotations, not guessed:
type->"command", assetId->"robot_id", mapId->"p2map_id",
cleanAll->"select_all", idMultipolys->"id_multipolys",
pmapVersionId->"user_p2mapv_id", spotGeometry->"geom",
favoriteId->"favorite_id". Command type values are lowercase strings
("clean", "start", "pause", etc.) — a couple are surprising:
CLEAN_SPOT serializes as "point_clean", not "clean_spot".

I never got further than this — the actual native dispatch
(CommandTierAgentImpl::postCommand()) calls into several layers of
unexported static functions with no symbols, past what's reasonable to
chase with objdump alone (no Ghidra/IDA available in my environment).
If you've already gone further on this, I'd love to compare notes.

**p2maps map bundles are tar.gz archives, not JSON.** Found via
P2MapAPI.MapUnpacker — fetchMapBundleContentHolder downloads from a
"pre-signed URL" and untars it. The endpoint that actually returns
that pre-signed URL (given a mapId+mapVersion) wasn't recoverable —
the wrapping coroutine method failed to decompile cleanly (jadx gave
up on the state machine), unlike its sibling fetchActiveVersions,
which decompiled fine and gave me a confirmed
GET /v1/p2maps?robotId={id}&visible=true.

**Live map streaming doesn't use the REST response's topic field at
all.** I'd assumed it did (a LiveMapStreamResponse.mqtt_topic field
exists in the API contract) but checking the app's actual usage, that
field is parsed and never read anywhere. The real subscribe call is
mqttClient.subscribe(MQTTTopicPrefixType.irbt, "livemap/update",
assetId) against a fixed topic pattern, and the REST call
(GET /v1/p2maps/livemap) is a periodic keep-alive ping instead.

Everything else (auth flow, AWS SigV4 signing for REST, the p2maps
edit-command vocabulary) is in the repo with full docstrings on
confidence level and how each thing was derived. Happy to share the
decompiled sources / native libs if useful for your own work, or to
run any diagnostic script you'd want against a real account if you
have test hardware and are willing.

Thanks for whatever you can share when you get a chance to test
against real hardware — even a single captured shadow-update payload
for a start command would resolve the biggest remaining unknown.

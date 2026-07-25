# Evidence trail

The research narrative behind roombapy-prime's confirmed and unconfirmed
findings: how each conclusion was reached, what was corrected along the
way, and what remains open.

**This is the long form. It was moved out of the source docstrings, not
written separately** -- the code had reached 36% docstrings, with
individual entries over a hundred lines, which made the modules hard to
read and hard to search (a code search mostly returned prose).

What stays in the code is the part you need while reading the code: what
a thing is, how confident we are, and any active warning. What lives here
is what you need while investigating: the derivation, the dead ends, the
session-by-session corrections. Both matter -- they just have different
readers.

**When adding a finding:** put the one-paragraph conclusion and its
confidence level in the docstring, and the reasoning here, under a
heading matching `module.Symbol`.

## Contents

**auth**

- [LoginResult](#authloginresult)
- [_raise_clear_ssl_error](#auth_raise_clear_ssl_error)

**diagnostics**

- [_check_candidate_shadows](#diagnostics_check_candidate_shadows)

**livemap**

- [MapUpdateMessage](#livemapmapupdatemessage)
- [from_json](#livemapfrom_json)

**map_bundle**

- [PolicyZoneFeature](#map_bundlepolicyzonefeature)

**map_editing**

- [SetRoomMetadataV1](#map_editingsetroommetadatav1)

**mission_control**

- [CommandParams](#mission_controlcommandparams)
- [RoutineCommand](#mission_controlroutinecommand)

**mission_history**

- [MissionTimelineReport](#mission_historymissiontimelinereport)

**mqtt_client**

- [cmd_topic](#mqtt_clientcmd_topic)
- [get_shadow](#mqtt_clientget_shadow)
- [mission_timeline_topic](#mqtt_clientmission_timeline_topic)
- [publish_cmd_payload](#mqtt_clientpublish_cmd_payload)

**prime_robot**

- [get_named_shadow](#prime_robotget_named_shadow)
- [send_mission_command](#prime_robotsend_mission_command)
- [send_routine_command_via_cmd_topic](#prime_robotsend_routine_command_via_cmd_topic)
- [send_simple_command](#prime_robotsend_simple_command)
- [send_umi_get_request](#prime_robotsend_umi_get_request)
- [trigger_echo_via_shadow](#prime_robottrigger_echo_via_shadow)
- [watch_live_map](#prime_robotwatch_live_map)
- [watch_mission_timeline](#prime_robotwatch_mission_timeline)
- [watch_named_shadows_updates](#prime_robotwatch_named_shadows_updates)
- [watch_state](#prime_robotwatch_state)

**rest_client**

- [get_map_geojson_link](#rest_clientget_map_geojson_link)

**robot_info**

- [ConnectionStatusShadow](#robot_infoconnectionstatusshadow)
- [DockState](#robot_infodockstate)
- [RobotStatusV2](#robot_inforobotstatusv2)

---


## auth._raise_clear_ssl_error

`roombapy_prime/auth.py`

NEW (V4/Prime prep, ha_roomba_plus login consolidation). Carried
    over from ha_roomba_plus's cloud_api.py::_raise_clear_ssl_error()
    (v3.5.0 bug-hunt fix, real-world report from wecoyote5: iRobot's
    own disc-prod.iot.irobotapi.com TLS certificate briefly expired on
    their end -- not a bug in the calling code, and not something a
    user can fix locally). Belongs here rather than only in
    ha_roomba_plus: every consumer of this library hits the exact same
    endpoints, including chairstacker/jadestar1864 running the
    standalone verify-* scripts directly, not just through Roomba+.

    Deliberately does NOT offer any way to skip/ignore certificate
    verification -- that would remove protection against
    man-in-the-middle attacks for every future connection this
    library ever makes.

    CORRECTED (this session, prompted by a real field report): the
    previous message asserted this was "almost always a temporary
    problem on iRobot's servers ... not something wrong with your
    setup". That claim came from generalizing the ONE case that
    prompted this function (wecoyote5, where iRobot's certificate had
    genuinely expired) -- and it is simply not knowable from here. A
    tester on macOS hit this repeatedly across sessions and versions,
    where the far likelier cause is local: Python installed from
    python.org does not use the macOS keychain, it ships its own CA
    bundle that has to be installed once by running "Install
    Certificates.command". Until that is run, EVERY TLS verification
    fails, permanently -- and our message was confidently telling them
    to wait a few hours for someone else to fix it.

    OpenSSL's own verify_message distinguishes these cases precisely,
    so we now branch on it instead of guessing: a missing local issuer
    means the trust store, an expired certificate means the server.


## auth.LoginResult

`roombapy_prime/auth.py`

http_base is carried through from the discovery response (the same
    value used internally for the /v2/login POST). http_base_auth is a
    SEPARATE field (see rest_client.py) -- confirmed pattern from
    ha_roomba_plus's cloud_api.py: httpBase is for /v2/login only,
    httpBaseAuth is the base for all authenticated data endpoints. Using
    http_base for both (an earlier mistake in this module) would be
    wrong for anything beyond login itself.

    credentials: AWS Cognito credentials for SigV4-signing REST calls
    (see CloudCredentials) -- required, not optional, mirroring
    cloud_api.py's "validate at the gate" lesson (a response missing
    these should fail loudly at login(), not with a confusing KeyError
    deep inside a later REST call).

    irbt_topic_prefix / iot_topic_prefix: CONFIRMED (session 43, see
    below) -- this field's long uncertainty is resolved. Needed to
    build MQTT topics outside the shadow system (mission commands via
    cmd_topic(), the live map topic via livemap_topic()).

    UPDATE (session 36): traced the two underlying native constants
    (core::ServiceDiscoveryImpl::kIotTopicPrefixFieldName /
    kIrbtTopicPrefixFieldName) further via native disassembly. Found
    them used as key arguments to a generic
    `AccountServiceImpl::sendUserRequest(key, callback)` call inside
    `onAccountInfoRefreshed()`, right alongside near-identical
    conditional checks for account country/locale/notification-center/
    commercial-messages settings -- a pattern that reads more like "sync
    this one account attribute via its own request if a pending-change
    flag is set" than "read this key out of the discovery response
    body". This is new, genuine context, but doesn't resolve the
    original question -- if anything, it opens a competing hypothesis
    (these values might come from a follow-up account-info fetch,
    not from ServiceDiscoveryData/login discovery directly) that
    wasn't previously considered. The literal JSON key string itself
    remains unfound either way (it's stored in a std::string bss
    global, filled in by a static initializer that couldn't be
    isolated among the many other things AccountServiceImpl's
    translation unit initializes at load time). Still needs either a
    real traffic capture or a substantially deeper native trace to
    resolve -- not further pursued this session.

    UPDATE (session 39): the underlying CONCEPT and its NECESSITY are
    now much more strongly evidenced, even though the literal JSON
    field name here is still unconfirmed. A live test (chairstacker)
    showed every mission command sent via update_shadow() (the classic
    shadow -- this library's previous best guess for mission control)
    timing out with zero response. Independently, this library's own
    native disassembly (objdump on libcorebase.so) found the literal
    format string "/things/%s/cmd" -- a topic family entirely separate
    from the shadow system, requiring exactly this kind of prefix.
    Separately, a third-party, unaffiliated GitHub project
    (lvigilantecorreo-commits/roomba-v4, MIT-licensed, author reports
    the command actually moving a real robot) documents the same shape
    explicitly: "{irbt_topics}/things/{BLID}/cmd", confirming the
    prefix is genuinely required for mission control, not just the
    live-map topic as previously assumed. This is an external,
    unverified-by-us source, but its topic pattern independently
    matches this library's own native string discovery -- see
    mqtt_client.py's cmd_topic()/publish_cmd() docstrings for the full
    trail and prime_robot.py's send_simple_command() for the new,
    corrected mission-control path built on this. The literal
    discovery-response JSON key itself remains the same long-standing
    guess ("irbtTopicPrefix"/"iotTopicPrefix") -- not resolved by any
    of this, only its importance is now much clearer.

    UPDATE (session 43): DEFINITIVELY RESOLVED. chairstacker's
    diagnostics run (using the new _report_topic_prefix_status()
    reporting from session 41) showed the guessed keys really were
    wrong, and the follow-up --dump-config capture showed the actual
    deployment object in full. The real keys are "irbtTopics" and
    "iotTopics" (plural "Topics", not "TopicPrefix" as guessed --
    close, but not exact). Confirmed real values from a live account:
    `irbtTopics: "v011-irbthbu"`, `iotTopics: "$aws"`. Two things this
    also confirms in passing: (1) "v011" matches the same account's
    `svcDeplId: "v011"` -- the same correlation already suspected from
    session 28's "v007" observation on a different account, now
    confirmed as a general pattern (`irbtTopics ==
    f"{svcDeplId}-irbthbu"`), though the field itself should still be
    read directly rather than reconstructed from svcDeplId. (2) the
    "v011-irbthbu" value is byte-for-byte identical to the example
    value shown in the third-party GitHub project cited in the
    thirty-ninth session's update -- as strong a confirmation as this
    project could hope for that project's corroboration was genuine,
    not coincidental. `login()` updated to read the correct keys.

    UPDATE (session 52): a fourth, independent confirmation, this time
    directly from the app's own bytecode rather than live/external
    data. A systematic `$$serializer` scan (the same technique behind
    most of this project's other confirmed models) found
    `DiscoveryResponse$Deployment$$serializer`, whose confirmed fields
    include `iotTopics`/`irbtTopics` -- an exact, direct bytecode match
    for the field names chairstacker's real account had already
    settled. This closes the loop about as completely as this kind of
    question can be closed: live account data, an independent
    third-party project, and the app's own compiled source all agree.


## diagnostics._check_candidate_shadows

`roombapy_prime/diagnostics.py`

NEW CANDIDATES (this session, a separate native-analysis track):
    MQTTTopics.java builds topics for FOUR MORE shadows this project
    never knew existed -- "ro-currentstate", "ro-stats", "ro-services",
    "ro-configinfo" (read-only, unlike the "rw-" ones above). These
    never appeared in the app's own command config for an identifiable
    reason: that config only lists commands, and nothing writes to a
    read-only shadow -- the wildcard-based enumeration that found the
    five "rw-"/classic shadows structurally could never have found
    these. "ro-currentstate" is now the strongest lead this
    investigation has had: the name itself describes exactly the kind
    of data being searched for. NOT YET TESTED against a real device
    as of this writing.

    Purely a read, same risk profile as get_state()/get_settings() --
    see get_named_shadow()'s own docstring for the specific earlier
    mistake ("rw-constatus" was wrongly written off originally because
    the app's command config lists only a write-side command for it --
    that describes commands, not subscriptions) that led to checking
    it at all; the same distinction (config lists commands, not
    subscriptions) is exactly why the four new "ro-" candidates were
    missed for as long as they were. Factored out as its own function
    (rather than an inline loop in run()) specifically so it's
    unit-testable on its own -- run() as a whole has no dedicated test
    of its own, this way the new behavior still does.


## livemap.MapUpdateMessage

`roombapy_prime/models/livemap.py`

CONFIRMED LIVE (this session, jayjay13011, roombapy-prime v0.1.11a6):
    real messages also carry an outer "timestamp" and a sibling
    "livemap_url_raw" alongside "livemap_url" -- both added here, not
    previously modeled. livemap_url is a presigned S3 URL ending in
    "p2mapv_geojson.tgz" -- the EXACT SAME format
    download_map_bundle()/parse_map_bundle() already handle for
    REST-fetched bundles; no new download/parsing code is needed to
    consume this live feed. livemap_url_raw points to a sibling
    "rawmap" path. Both URLs' paths are fixed/generic per robot
    (".../dload_livemap/{blid}/..."), not versioned per-update -- only
    the query-string signing differs between messages, confirmed by
    direct comparison, not assumed.

    "rawmap" FORMAT, FULLY DECODED (this session, chairstacker, from a
    hexdump of a file saved during an earlier run -- the actual map
    content was never shared, only structural bytes/strings). This is
    a Protocol Buffers message, not a raw occupancy grid directly (the
    earlier "raw grid, one byte per file" hypothesis was wrong about
    the FILE as a whole, but right about what's embedded inside it).
    Confirmed structure, hand-decoded against the real hexdump and
    verified with a synthetic reconstruction matching it exactly:

        field 2 -> nested message: two Unix timestamps (map created/
                   updated), and a sub-message (field 7) containing
                   the map_id as a 32-char hex string
        field 3 -> nested message: a plain-int map_id-suffix
                   timestamp, then width and height as plain varints
                   (440 x 400 in the one real example), then five
                   float32 fields -- almost certainly origin_x,
                   origin_y, and other bounds/rotation values, with
                   the smallest positive one (0.05) being the
                   resolution in metres/cell -- a completely standard
                   SLAM occupancy-grid value (5cm/cell)
        field 4 -> wraps exactly one bytes field (field 1): the
                   occupancy grid itself, width*height bytes, one byte
                   per cell -- 176000 bytes in the real example,
                   EXACTLY matching 440*400, confirmed directly rather
                   than assumed

    "Clean Kitchen" (a room name) and "Map1"/"Map2" also appeared as
    plain strings elsewhere in the file (via `strings`) -- not yet
    located precisely in the field layout above, presumably a sibling
    field carrying room-name/multi-map metadata this session didn't
    reach. `models/livemap.py` doesn't yet parse this structure into a
    dataclass -- `decode_rawmap.py` (a standalone script, not part of
    the library) exists to extract and render the grid for
    confirmation first, before committing to field names here.

    VISUALLY CONFIRMED (this session, chairstacker): the rendered PNG
    (width x height orientation, not the swapped one) IS a recognizable
    floor plan matching their real home -- the strongest possible
    confirmation of the whole structure above. One correction found in
    the process: the raw byte order renders vertically flipped
    relative to the app's own map view (row 0 at the top in image
    convention vs. row 0 at the bottom in the occupancy grid's own
    convention, a common mismatch) -- decode_rawmap.py now flips the
    image before saving so its output matches the app's orientation
    directly. Also reported: rougher edges and a few unexplained white
    streaks in areas with no carpets/furniture, compared to the app's
    own cleaner rendering -- plausibly SLAM sensor noise (reflective
    surfaces, specular floor reflections) or an unknown/low-confidence
    occupancy value rendering as a distinct shade rather than a
    rendering bug, consistent with the byte histogram showing far more
    than just two values (a simple free/occupied grid would show only
    two, not a whole distribution) -- not confirmed further, no reason
    yet to think the decode itself is wrong given the floor plan itself
    is unmistakably recognizable.

    NOT YET USED for anything beyond this model -- no entity in
    ha_roomba_plus consumes it yet. A concrete next step this makes
    possible: a live-updating map/camera entity, refreshed from
    whatever the most recent MapUpdateMessage delivered, using
    download_map_bundle()/parse_map_bundle() directly against
    livemap_url -- no new download or parsing code needed. Now that
    rawmap's structure is understood AND visually confirmed, an
    occupancy-grid-based rendering (or a room-outline overlay combining
    both this and the GeoJSON bundle) becomes a real, evidenced option
    -- not yet designed or built.


## livemap.from_json

`roombapy_prime/models/livemap.py`

cur_path length must be (2 + 4*n) for n position points --
        exactly as checked in PositionUpdatesSerializer.deserialize().
        Orientation is shifted by +pi, same as in the original -- the
        reason for this convention wasn't further investigated.

        CONFIRMED LIVE (this session, jayjay13011, roombapy-prime
        v0.1.11a6 -- the first capture with topic tracking, so the
        exact topic this arrives on is now also settled, see
        livemap_topic()/watch_live_map()). This directly resolves the
        TENSION noted below in favor of option (a): the flat cur_path
        array genuinely IS the wire format, not a misreading -- a real
        capture confirms it exactly, including operating_modes
        actually varying (not a fixed constant): 0 for the first ~5
        seconds of cleaning (still settling in after travel/reloc),
        then switching to 5 for the rest of the observed cleaning
        period. The switch happens a few seconds AFTER
        mission/timeline/report's own "room" event fires, not
        precisely at that boundary -- plausibly a finer-grained
        sub-state (e.g. "orienting" vs "actively cleaning") than what
        the mission-timeline channel exposes, but this is not
        confirmed, just a reasonable reading of the timing.

        IMPORTANT TENSION, discovered but NOT resolved (session 48):
        a systematic `$$serializer` scan found
        `PositionUpdates$PositionUpdate$$serializer` -- an
        AUTO-GENERATED serializer (unlike the CUSTOM
        `PositionUpdatesSerializer` this method's cur_path-flat-array
        parsing was originally based on) -- with confirmed fields
        `point`/`orientation`/`operatingModes`. This is suspiciously
        close to this library's OWN `PositionSample` dataclass (point/
        orientation/operating_modes), which was built to match the
        cur_path-derived values, not copied from this serializer.
        Two real possibilities, neither confirmed: (a) the actual wire
        format for each position update is a structured JSON object
        matching PositionUpdate's confirmed fields directly, and the
        flat "cur_path" array parsing here is based on an earlier,
        possibly mistaken reading of the custom serializer's logic;
        or (b) both genuinely coexist -- the custom
        `PositionUpdatesSerializer` might pack/unpack a LIST of these
        structured PositionUpdate objects specifically into the flat
        "cur_path" wire array as an optimization, with PositionUpdate
        only ever existing as the in-memory Kotlin representation, not
        a JSON shape of its own. The live capture above settles which
        of these is right for the WIRE FORMAT (flat array, confirmed);
        it doesn't settle whether PositionUpdate the class still
        exists internally in the app for the same data.


## map_bundle.PolicyZoneFeature

`roombapy_prime/models/map_bundle.py`

properties.zone_type == "KeepOutZone" + geometry is Polygon
            -> a keep-out zone (-> VirtualWallRectangleV1 on the write side)
        properties.zone_type == "KeepOutZone" + geometry is LineString
            -> a virtual wall (-> VirtualWallLinearV1 on the write side)
        properties.zone_type == "NoMopZone" (always Polygon)
            -> a no-mop zone (-> VirtualWallNoMopZoneV1 on the write side)
        properties.zone_type == "Threshold" (always Polygon)
            -> a threshold, not part of the virtual-wall family at all

    Each category's real code also skips a feature silently (no error)
    if its id or geometry is missing/None -- not modeled here, callers
    should expect from_json() to potentially need graceful handling of
    genuinely incomplete features.

    GEOMETRY CONFIRMED TO PASS THROUGH UNCHANGED at every later stage
    -- reading this feature, converting it to a VirtualWallV1 subtype,
    and sending it back via SetVirtualWalls all use the exact same
    coordinate values, no transformation, no rescaling. This also
    answers CommandPolygon.poly's own previously-unconfirmed
    coordinate system: it's whatever this geometry's own coordinate
    system already is (still not independently pinned to a specific
    unit/origin, but confirmed to be a SINGLE, consistent system
    throughout, not something CommandPolygon transforms into
    separately).


## map_editing.SetRoomMetadataV1

`roombapy_prime/models/map_editing.py`

CONFIRMED (live APK decompilation, this session, down to the
    actual P2MapRoomMetadata$Serializer.serialize() call): params are
    {"room_id": ..., "room_metadata": {...}} under command
    "set_room_metadata" -- room_id sits alongside room_metadata, NOT
    nested inside it (the serializer reads value.getMetadata().getId()
    separately for the outer room_id). room_metadata itself has
    EXACTLY two possible keys, both written only when not None:
    "name" (str) and "type" (RoomCategory, see enums_common.py).
    Nothing else -- no id, no other fields -- goes into room_metadata.

    THE CURRENT APP'S ACTUAL ROOM-EDIT PATH: both room renaming AND
    room-category changes go through SetRoomMetadata now, not
    RenameRoomV1/SetRoomTypeV1 (see those classes' own docstrings for
    the deprecation finding -- SetRoomMetadata replaces BOTH of them).

    CONFIRMED CONSTRAINT: the underlying constructor requires at least
    one of name/type to be set (both individually may be None, but not
    both at once) -- enforced here too via __post_init__, so a caller
    gets a clear, immediate ValueError instead of a request the server
    would have to reject. A None field is OMITTED from room_metadata
    entirely (not sent as JSON null) -- this is a genuine partial-
    update: you can change just the name, just the category, or both,
    but never explicitly clear one back to empty this way.

    `type` uses RoomCategory (enums_common.py), NOT the RoomType used
    by SetRoomTypeV1 -- these are two unrelated enums for the same
    real-world concept, with different wire representations (int codes
    vs. snake_case strings). See RoomCategory's own docstring for why
    that distinction matters and the specific mistake it guards against
    (an earlier draft of this class conflated RoomType with the
    similarly-named-but-unrelated RegionType, caught before shipping --
    see CHANGELOG).


## mission_control.RoutineCommand

`roombapy_prime/models/mission_control.py`

CORRECTED (eleventh session, via cross-checking with
    ha_roomba_plus): "ordered" is NOT an indication of sequencing
    multiple separately-sent RoutineCommand objects (e.g. from a
    FavoriteV1/Routine.commandDefs list). ha_roomba_plus (verified
    against real Classic devices in production for years) uses
    "ordered" as an INTRA-command property alongside "regions" within
    the same command object: whether the regions WITHIN this one
    command should be visited in listed order, or the robot itself is
    allowed to optimize. Whether multiple commandDefs entries are
    actually sent as separate, sequential commands thus remains
    UNRESOLVED -- "ordered" is not evidence for that.

    params/regions/id_multipolys accept either the bytecode-confirmed
    types (CommandPolygon/CommandParams/Region, see below in this
    module) or still raw dicts (backward-compatible escape hatch for
    anything not covered by the typed models). CORRECTED (this
    session, parallel native-analysis track): this docstring
    previously said these "wasn't modeled in detail" -- stale as of
    several sessions ago; Region/CommandPolygon/CommandParams are all
    fully modeled below, this Union type is deliberate flexibility,
    not an admission of missing work.


## mission_control.CommandParams

`roombapy_prime/models/mission_control.py`

CORRECTED (this session, parallel native-analysis track,
    $$serializer.<clinit> inspection -- superseding an earlier "DEX
    field list" reading this docstring used to cite): 18 of this
    class's wire keys were wrong, not just differently-cased. The
    earlier reading had read Kotlin PROPERTY names from the class
    declaration, not the actual @SerialName wire keys -- two different
    things in kotlinx.serialization, and critically, undeclared keys
    are silently DROPPED by the deserializer rather than erroring. A
    RoutineCommand built with the old keys would have had these 18
    parameters vanish entirely on arrival, not just look slightly
    different -- a real functional bug for anything using CommandParams
    (which sits inside every region of a region-aware command), not a
    cosmetic one. Corrected in both to_json() and from_json() below;
    see to_json()'s own docstring for the full before/after list.
    GENERAL LESSON, worth remembering for future bytecode findings in
    this project: a DEX/property-declaration reading is not the same
    as a wire-key confirmation -- always check the actual
    $$serializer.<clinit> table, not just the class's own field list.

    CONFIRMED (same session, onlyUserModifiableParams()/
    onlyNonUserModifiableParams()): exactly seven fields are
    NON-user-modifiable (system/metadata, kept as-is when the rest of
    a command is edited) -- routine_type, clean_score_id,
    smart_clean_id, replay_of, routine_modified, adaptive_cleaning,
    cleaning_profile. Every other field on this class is
    user-modifiable and factors into the real app's own
    modified-vs-unmodified comparison (see routine_modified's own
    field docstring below for how that comparison actually works).

    SPECIAL CASE, deliberately NOT touched by the correction above:
    no_auto_passes (wire key noAutoPasses) does not appear in the
    confirmed serializer list at all -- that list has
    no_persistent_pass (now corrected to wire key "noPP") instead.
    These are confirmed to be two genuinely DIFFERENT fields, not a
    spelling variant of each other -- checked directly against the
    Kotlin class's own field list, which has both separately.
    no_auto_passes is kept exactly as it was because it's
    independently confirmed from real live data (chairstacker's
    cleanSchedule2[].cmdStr, session 27), not from this bytecode
    reading -- a case where the field-list correction specifically
    does NOT apply, verified rather than assumed.

    suction_level/carpet_boost, RESOLVED (parallel native-analysis
    track, SuctionLevel.java + a follow-up investigation that first
    found CarpetBoostSettings.java's own three-way enum
    (PERFORMANCE/ECO/AUTO), then confirmed it CONFIRMED DEAD CODE --
    zero consumers anywhere, part of an older View/Fragment/XML UI
    generation superseded by Compose, still compiled into the APK but
    never instantiated -- see CarpetBoostSettings's own docstring):
    suction_level is a purely numeric enum, Invalid(0)/Low(1)/
    Medium(2)/High(3)/Turbo(4) -- 0 is an explicit ERROR/placeholder
    value, NOT "Auto". carpet_boost really is the plain bool this
    class already models, confirmed by cross-referencing iRobot's own
    public product documentation for the real "Carpet Boost" feature:
    a SENSOR-DRIVEN, REAL-TIME modifier -- the robot detects carpet
    (via increased brush resistance transitioning from hard floor) and
    automatically increases suction power for as long as it's on
    carpet, independent of the manually-set suction_level, reverting
    on hard floor. This is why suction_level itself has no "Auto"
    value at all: floor-type adaptation isn't a suction_level concept
    in the first place, it's this entirely separate boolean toggle
    (enable/disable the feature; the robot's own sensors decide WHEN
    to actually apply the boost, not the app).

    A THIRD, separate field worth knowing about here: adaptive_cleaning
    (isolated, only referenced in RoutineCommand's own source) is
    plausibly the wire form of iRobot's distinct "Adaptive Cleaning"/
    "Dirt Detective" feature -- HISTORY-based (learns from past
    cleaning jobs to prioritize dirtier rooms), not real-time/sensor-
    based the way carpet_boost is. Genuinely different mechanisms
    despite both being "the robot adjusts itself" concepts -- don't
    conflate them if surfacing either as a user-facing setting.

    CRITICAL, CONFIRMED FINDING ABOUT WHAT THE REAL APP SENDS ON A
    PLAIN "START" TAP (parallel native-analysis track,
    SpaceDetailsAggregateViewModel.currentDefaultCommandParams()):
    the real app does NOT omit CommandParams for a basic start --
    it explicitly fetches the account's currently active preferences
    (availablePreferencesDataProvider.getFilteredPreferences(), itself
    populated by a real fetch() through missionRepository) and builds
    a full CommandParams from them before sending. This means
    send_simple_command()'s own bare {"command", "time", "initiator"}
    payload -- confirmed working for start/pause/stop/resume/dock/find
    at the TRANSPORT level -- is NOT equivalent to what the real app's
    own "Start" button does regarding suction/carpet-boost/etc: it
    structurally CANNOT carry these fields at all (a fundamentally
    different, simpler wire shape than RoutineCommand, not just a
    missing optional field), so whatever suction/carpet-boost setting
    the robot ends up running with is decided ENTIRELY by the robot's
    own internal fallback, not by mirroring the account's actual saved
    preferences the way the real app's own flow does. A field report
    of a mission unexpectedly always running at what looked like
    maximum power, never adapting, is CONSISTENT with this gap -- not
    confirmed as the definitive explanation, but a real, structural
    reason to expect a difference here, not just a coincidence.
    Reaching real preference-aware parity would need the
    RoutineCommand-carrying path (send_routine_command_via_cmd_topic(),
    itself still not live-tested for ANY use as of this writing) to
    become the basic-start path too, not just the region-aware one --
    a real architectural gap, not just a documentation one.


## mission_history.MissionTimelineReport

`roombapy_prime/models/mission_history.py`

A valuable cross-confirmation neither investigation alone
    established: this wraps the SAME MissionTimelineEvent model already
    confirmed (session 18/31, via androguard/jadx static analysis) for
    get_mission_history()'s HISTORICAL timeline data -- the live push
    channel and the historical pull endpoint evidently share one
    underlying event schema. RoomEvent/TravelEvent/TentativeLocationEvent
    (room/travel/reloc) all matched the live capture's fields exactly,
    with zero corrections needed.

    event: in every live message captured so far, ALWAYS exactly one
    entry -- the newest/current event. fin_events: a growing list of
    PAST events, each gaining an end_time (MissionTimelineEvent's own
    "ets" field) once superseded by the next one -- effectively a
    running history of the mission-so-far, resent in full on every
    single update rather than delta-only.

    command/initiator/command_time: NOT new data -- this is the SAME
    payload send_simple_command() itself publishes (see
    mqtt_client.py's publish_cmd()), echoed back here as context for
    which command's mission this report belongs to.

    n_missions ("nMssn" on the wire): meaning still not directly
    confirmed (a lifetime mission counter remains the most plausible
    guess), but one earlier hypothesis is now DISPROVEN: a second live
    capture (chairstacker, same session as this class's original
    confirmation) showed 256 where the first had shown 255 -- ruling
    out "a saturating counter capped at the max value of an unsigned
    8-bit integer" as an explanation, since 256 exceeds that range. A
    genuine incrementing counter (whether lifetime missions or
    something else that increments once per mission) is now the better-
    supported reading.

    timelineRequestId (optional, observed on some but not all live
    report messages, chairstacker): appears tied to an explicit
    client-side request for a fresh timeline update -- also observed as
    its own bare {"timelineRequestId": N} message on the wildcard
    channel, separate from any mission/timeline/report envelope.
    Mechanism not further investigated; stored as an opaque int when
    present.

    mission_id ("01KXXQM8XZEDJ24701JF121CCH" observed): CONFIRMED as a
    real ULID (Universally Unique Lexicographically Sortable
    Identifier), not just a plausible shape match -- rigorously
    verified against BOTH mission_ids seen across two live captures:
    26 characters, every character in the Crockford base32 alphabet
    (which deliberately excludes I/L/O/U -- neither mission_id
    contains any of those four), first character in the valid 0-7
    range a ULID's 48-bit millisecond timestamp requires. Beyond the
    shape: the timestamp actually ENCODED in the first 10 characters
    was decoded directly (standard ULID timestamp decoding, Crockford
    base32) and compared against this same report's own cmd.time (the
    real Unix timestamp of the "start" command that began the
    mission) -- 0.0s and 3.6s apart on the two captures respectively.
    This is not a coincidental format match; the ULID's own embedded
    timestamp genuinely corresponds to when the mission it identifies
    actually began.

    map_version fields observed on nested events (RoomEvent.map_version
    etc., e.g. "260719T174414.994"): decodes cleanly as YYMMDD"T"HHMMSS.mmm
    -- confirmed against two independent real captures (this session's
    "260719T174353.832" = 2026-07-19 17:43:53.832, matching the actual
    capture date; and an existing test fixture's "260715T130113.944" =
    2026-07-15 13:01:13.944). Each event in a single live capture had a
    DIFFERENT map_version despite sharing the same map_id -- suggesting
    this is a per-localization-update timestamp, not a "map was edited"
    version the way the name might suggest.


## robot_info.ConnectionStatusShadow

`roombapy_prime/models/robot_info.py`

"echo" AS A CHIME TRIGGER -- ALSO DISPROVEN (chairstacker, real
    device test): writing True to this field produced a genuine,
    accepted shadow write (a real update/delta response came back),
    but the robot did NOT chime -- and "locate" from the real app
    worked fine on the same device immediately after. See
    PrimeRobot.trigger_echo_via_shadow()'s own docstring for the full
    result. What "echo" actually represents remains unresolved --
    possibly a connectivity heartbeat/ping (consistent with the rest
    of this shadow being about connection status), not necessarily
    anything chime-related at all.

    TYPES CONFIRMED (parallel native-analysis track, Ghidra
    decompilation of the app's own constructor signatures, not
    guessed): connected/connected_v2 are both plain booleans.
    connected_v2's relationship to connected (newer replacement?
    different granularity?) is still not confirmed. echo is
    PROBABLY also a boolean (a packed flag in the decompiled
    constructor, slightly less certain than the other two but not
    contradicted by anything) -- kept as a plain bool here rather than
    Any, consistent with how confident this specific finding is.


## robot_info.DockState

`roombapy_prime/models/robot_info.py`

Four functional-area bands, matching the numeric-range pattern
    already observed in DockStatus's own real captured values (state/
    pw_state/pd_state = 301/601/701): DOCK_* (general dock, 300s, plus
    two low outliers at 0-3 shared with the pad-wash/pad-dry bands --
    see the duplicate-value note below), FLUID_REPLENISHMENT_* (400s),
    PAD_WASH_* (600s), PAD_DRY_* (700s).

    CONFIRMS DockStatus's own real captured values directly:
    state=301 -> DOCK_READY, pw_state=601 -> PAD_WASH_OKAY,
    pd_state=701 -> PAD_DRY_OKAY -- chairstacker's device was
    dock-ready with both pad subsystems in their own "okay" (idle,
    no error) state at capture time. What was previously only an
    "OBSERVATION, NOT A CONFIRMED MAPPING" (see DockStatus's own
    docstring) about which numeric band belongs to which category is
    now a directly confirmed, named value for each of the three
    fields captured live.

    DUPLICATE VALUES, CONFIRMED PRESENT IN THE REAL ENUM ITSELF (NOT A
    TRANSCRIPTION ERROR): 2 is shared by PAD_DRY_UNHEATED_AIR and
    PAD_WASH_NORMAL_HEATED_WATER; 3 is shared by PAD_DRY_HEATED_AIR and
    PAD_WASH_MAX_HEATED_WATER. Plausibly context-dependent (meaningful
    only within whichever specific field/subsystem reports it, not
    globally unique) -- not independently confirmed which
    interpretation is correct, only that the duplication itself is
    real. Python's own IntEnum aliasing applies here: both names for
    each duplicated value remain accessible as class attributes, but
    DockState(2)/DockState(3) themselves resolve to whichever name is
    listed first below (the PAD_DRY_* one, alphabetically/positionally
    earlier here) -- an artifact of Python enum mechanics, not
    evidence that one name is somehow more "correct" than the other.


## robot_info.RobotStatusV2

`roombapy_prime/models/robot_info.py`

UPDATE (session 49): the four list/dict-typed fields' own element
    types are now ALSO confirmed (DockControl/RobotStatusButton/
    RobotStatusError, see their own docstrings) -- previously left as
    list[Any], now properly typed.

    STRONGER NEGATIVE EVIDENCE (this session, jayjay13011, roombapy-prime
    v0.1.11a6): a live capture with fully topic-tracked wildcard coverage
    (7 distinct topics identified: mission/timeline/report, livemap/update,
    livemap/cmd, filexfer_req, filexfer_resp, cmd, service_event) watched
    for 300 seconds after stop+dock were sent -- specifically to give the
    robot time to physically reach its dock -- and NONE of these 7 topics
    carried anything battery/charging-related. This doesn't prove
    RobotStatusV2 is unreachable via MQTT (it could still live on a topic
    this particular wildcard scope doesn't cover, e.g. outside
    "things/{blid}/"), but it does rule out "we just weren't watching
    long enough" and "it's mixed in with one of these other message
    types but we didn't notice" as explanations. The most likely
    remaining possibilities: it's shadow/get_state()-only (never pushed),
    or it lives under a topic root this wildcard didn't reach.

    NAMED-SHADOW HYPOTHESIS DISPROVEN (this session, chairstacker, all
    five known named shadows checked in one pass via
    get_named_shadow()): "rw-constatus" was the leading candidate,
    reasoned from a native-app symbol trace showing RobotStatusV2's
    value assembled from four combined data streams rather than one
    ready-made field. Live content: {"connected", "connectedv2",
    "echo", "svcEndpoints"} -- this is MQTT/AWS-IoT CONNECTION status
    (is the device currently connected to the broker), not battery or
    charging status. The name's surface resemblance to "connection
    status" was accurate, but pointed at the wrong KIND of
    "connection" -- network connectivity, not power/charging state.
    The other two candidates also confirmed content, neither
    battery-related either: "rw-schedule" is just {"cleanSchedule2",
    "nsmip", "svcEndpoints"} (the cleaning schedule -- now modeled as
    ScheduleShadow, alongside ConnectionStatusShadow/SoftwareStatusShadow
    for the other two), "rw-software" is {"deploymentId",
    "deploymentMpkg", "deploymentState", "imuRecal", "lastCommand",
    "lastSwUpdate", "nsmip", "softwareVer", "subModSwVer",
    "svcEndpoints"} (OTA/firmware update status). All five named
    shadows this wildcard-subscription pattern covers are now fully
    enumerated -- none contain battery/charging/dock data. Whatever
    "AssetNetworkData"/"OTAStatusData" (from the same native trace)
    actually resolve to in the real app, they evidently aren't
    equivalent to "rw-constatus"/"rw-software" the way this hypothesis
    assumed, at least not for the battery-relevant portion of
    RobotStatusV2 specifically.

    ARCHITECTURE, CORRECTED (this session, parallel reverse-engineering
    track -- two earlier claims from that same track's own prior notes
    were explicitly retracted, not carried forward here: a "batPct"/
    "NetworkType.CLOUD" finding that turned out to belong to the
    Classic-layer RobotV1/RobotV2 classes, unrelated to Prime; and an
    unsupported "battery isn't available via the cloud at all" claim --
    logically untenable, since the app itself displays battery remotely,
    so SOME cloud channel must carry it). The actual, better-supported
    finding: the data lives in core::MissionData, a JNI proxy class
    (getBatteryLevelPercentage/getIsCharging/getIsFullyCharged/
    getTankLevel/getDockState/getResolvedMissionStatus/
    getCommandReadinessMap, plus dock descriptors) that itself must be
    FED from outside the native core -- a proxy doesn't invent values.
    Combined with SettingsData/AssetNetworkData/OTAStatusData via
    rxcpp::combine_latest into StatusReducerData -> this class -> UI.
    Structurally notable: this class has no $$serializer despite
    @SerialName-annotated fields -- those annotations describe the
    native-to-Kotlin handoff format (via ObservableUseCaseJsonCallback),
    NOT necessarily the cloud wire format directly.

    EXPANDED FIELD LIST (this session, from RobotStatusV2Constants.java
    directly -- meaningfully larger than the 11 fields modeled below,
    which predate this finding): battery_level, allowed_modes, buttons,
    conditional_errors, dock_controls, dock_info, command_readiness,
    cycle, asset_connection_state (a composite: robot_connected_to_iot,
    aws_network_state, app_to_robot_local, is_asset_missing_detected,
    status_error_code), dock_state_* (dock_id, evac_state,
    firmware_version, fluid_replenishment_state, capabilities, error).
    Not yet added as dataclass fields here -- documented so a future
    capture that DOES find this structure somewhere is recognized
    against the fuller list, not just the 11 already modeled.

    THE ACTUAL UNTESTED GAP (this session): every wildcard capture so
    far has only covered "{irbt_topic_prefix}/things/{blid}/#" -- the
    entire "$aws/" tree (where get_state()/get_settings() already build
    their OWN topics, under "$aws/things/{blid}/shadow", see
    _shadow_base() above) has never been wildcard-captured, and
    watch_state()'s update/delta push subscription has never been run
    LIVE during an active mission (see its own docstring's correction).
    One real device (chairstacker) showed a shadow version of 5324 --
    over five thousand updates, hard to explain for purely static
    configuration. verify_mission_timeline.py's --watch-shadow-delta
    and --watch-aws-tree flags exist to actually test this now.

    FOUND (this session, chairstacker, live -- the actual resolution
    of the search this whole docstring documents): the named shadow
    "ro-currentstate" (one of four previously-unknown read-only
    shadows found via MQTTTopics.java, see verify_named_shadows.py's
    own module docstring for that discovery) reports these keys:
    "batPct", "bin", "cleanMissionStatus", "detectedPad", "dock",
    "lastDisconnect", "p2maps", "regDate", "runtimeStats",
    "svcEndpoints", "tankPresent", "tz". "batPct" -- battery
    percentage -- is exactly what this entire investigation was
    searching for, and "dock"/"cleanMissionStatus" plausibly cover
    charging/docked state and live mission status respectively.
    "cleanMissionStatus" specifically matches the exact event name
    this project's own native decompilation found on
    AssetIotTopicFactory months earlier (session covering
    mission/timeline/report's own discovery) -- two independent
    findings now pointing at the same underlying concept from
    different angles.

    A NOTE ON THE EARLIER RETRACTION ABOVE: this session's own
    "ARCHITECTURE, CORRECTED" paragraph above retracted an earlier
    parallel-track claim that a "batPct" finding belonged to the
    Classic-layer RobotV1/RobotV2 classes, unrelated to Prime. That
    retraction concerned a SPECIFIC claim about WHERE a particular
    piece of decompiled code lived (Classic-only source), not a
    claim that the field NAME "batPct" could never appear on a Prime
    device's own cloud data -- iRobot plausibly reuses the same field
    vocabulary across Classic and Prime cloud infrastructure even
    where the underlying delivery mechanism differs. This live
    "ro-currentstate" result is a directly-observed key on a real
    Prime device's own named shadow, independent of and not
    contradicted by that earlier retraction.

    STILL UNCONFIRMED: only the KEY NAMES are known so far (from
    get_named_shadow()'s reported-keys summary) -- the actual VALUES
    (is batPct 0-100? an int or a string? does "dock" mean boolean
    docked-or-not, or something richer?) have not yet been seen. A
    follow-up request for the full reported payload (not just the key
    list) is the natural next step before modeling this shadow's
    content as a proper dataclass.


## mqtt_client.cmd_topic

`roombapy_prime/mqtt_client.py`

CORRECTED basis: a live test against a real account
        (chairstacker, session 39) showed every attempt via
        update_shadow() timing out with zero response (not even
        /rejected) -- consistent with publishing to a topic the shadow
        service doesn't recognize at all, not a permission or payload
        problem on an otherwise-correct topic. Independently, this
        library's own native disassembly (objdump on libcorebase.so)
        found the literal format string "/things/%s/cmd" -- a
        DIFFERENT topic family from "$aws/things/%s/shadow/update"
        (which does exist in liblegacyCore.so, but is presumably used
        for something else, e.g. the settings/schedule "delta"
        mechanism, not mission commands). This matches
        base_roomba_config.json's own "topic" field for mission-control
        commandIds ("Control", "AssetControlCommand"): the value is
        literally "cmd", a THIRD category distinct from "shadow" (used
        by GetThingShadow, confirmed working) and "delta" (used by
        settings/schedule commands) -- not just a coincidental label.

        Independently, a third-party, unaffiliated GitHub project
        (lvigilantecorreo-commits/roomba-v4, MIT-licensed, reverse-
        engineered via mitmproxy + APK strings + Ghidra, author reports
        the exact command actually moved a real robot) documents this
        same topic shape explicitly: "{irbt_topics}/things/{BLID}/cmd",
        with a simple payload {"command": ..., "time": ..., "initiator":
        ...} -- see publish_cmd()'s docstring. This is an external,
        unverified-by-us source, not an Anthropic/roombapy-prime
        finding -- but the topic pattern it describes independently
        matches this library's own native string discovery, which is
        the strongest kind of corroboration available without a live
        test of our own against this exact path.

        UPDATE (session 43): the "irbt_topic_prefix" VALUE extraction
        itself is now confirmed (see auth.py's LoginResult docstring --
        real field name "irbtTopics", real confirmed value
        "v011-irbthbu" from a live account, byte-identical to the
        third-party project's example value above). What remains
        UNCONFIRMED BY THIS LIBRARY ITSELF is whether the resulting
        topic, once correctly built, actually gets a real robot to
        react when published to -- that's the next thing a live test
        needs to settle.


## mqtt_client.mission_timeline_topic

`roombapy_prime/mqtt_client.py`

PROMPTED BY: a live idle-vs-mid-mission diff (chairstacker) that
        showed the classic shadow's reported state is byte-identical
        whether the robot is idle or actively cleaning -- proving that
        specific comparison (two point-in-time get_state() snapshots)
        doesn't move during a mission. CORRECTION (this session,
        parallel reverse-engineering track): this was previously
        over-stated as "live mission status does NOT flow through the
        shadow mechanism at all" -- that's broader than the evidence.
        The snapshot diff says nothing about whether the shadow's
        update/delta PUSH channel (watch_state()) sees intermediate
        changes; that specific test has never been run live during an
        active mission. This mission-timeline topic is believed to be
        the actual channel for it regardless, based on: (a) an
        "eventList" entry named "cleanMissionStatus"
        in base_roomba_config.json (matching the Classic protocol's own
        live-mission-status channel name), and (b) a decompiled native
        class, core::RobotMissionStatusEventImpl, whose constructor
        signature contains real per-mission fields (mission type,
        phase, readiness state, multiple counters/timestamps) --
        structurally nothing like the classic shadow's static
        capability data.

        report=True -> ".../mission/timeline/report" (the direction a
        robot would push status TO the cloud/subscriber -- what a
        caller watching for live status wants). report=False ->
        ".../mission/timeline/request" (the other half of the
        kRequest/kReport pair the native IotTopicType enum defines).

        UPDATE (this session, chairstacker): the request side is no
        longer just "included for completeness, not expected to be
        useful to subscribe to" -- a real message was captured on it
        during a wildcard watch: {"timelineRequestId": <int>}, a bare
        correlation ID (NOT a Unix timestamp -- checked directly,
        decodes to 2009, nowhere near this session's actual date).
        This is the standalone confirmation of the same field
        MissionTimelineReport.timeline_request_id (added in v0.1.11a6)
        already carries when embedded in a report -- meaning the two
        topics are a genuine, now-observed request/response pair: this
        topic carries the bare request correlation ID on its own,
        and a matching report (same ID) arrives separately on the
        report topic. Only the request SIDE'S payload shape is
        confirmed by this; still unconfirmed whether publishing to
        this topic ourselves (rather than just observing the robot's
        own traffic on it) would actually trigger a fresh report --
        not attempted.

        CONFIDENCE LEVEL, precisely: the topic NAME and its existence
        are confirmed from native symbols AND now from a live capture
        (the request side specifically, this session). Whether
        irbt_topic_prefix applies here the same way it does for
        cmd_topic() is now CONFIRMED, not just inferred (parallel
        APK-research chat, this session): decompiled call-site code
        for all three of AssetIotTopicFactory's topic-building methods
        shows the identical pattern -- the same stored constructor
        value (same memory offset) concatenated with each method's own
        suffix template. No structural difference between this topic
        and the live-confirmed cmd_topic(). The report side's payload
        shape (beyond timeline_request_id, which IS confirmed) is
        covered by watch_mission_timeline()'s own docstring in
        prime_robot.py.


## mqtt_client.publish_cmd_payload

`roombapy_prime/mqtt_client.py`

NOW WAITS FOR PUBACK AND RETURNS WHETHER IT ARRIVED (this
        session, per the parallel APK-research chat's own finding):
        QoS=1 was already set here, but nothing previously checked
        whether the broker actually acknowledged the publish at the
        MQTT PROTOCOL level. The "fire-and-forget" framing in this
        docstring's own prior wording conflated two different things:
        "no APPLICATION-level ack topic exists for this command
        family" (still true -- there is no cmd/accepted topic) with
        "no PROTOCOL-level ack either" (false -- QoS 1 IS a PUBACK
        guarantee, entirely independent of whatever the robot/app
        does with the payload afterward). This matters specifically
        because rejected/report is published BY THE ROBOT when it
        receives and rejects a command -- if the broker's IoT policy
        silently drops the publish before it ever reaches the robot,
        the robot never sees it and therefore can't reject it either.
        "No rejection" was previously treated as at least partial
        evidence the command was delivered; it is NOT -- it's equally
        consistent with "never delivered at all". Checking the PUBACK
        directly answers the delivery half of that question, leaving
        only "robot received it but ignored it" as the remaining
        possibility when PUBACK succeeds.

        Returns True if the broker confirmed receipt (PUBACK) within
        confirm_timeout seconds, False otherwise (queue full, publish
        failure, or no confirmation within the timeout).


## mqtt_client.get_shadow

`roombapy_prime/mqtt_client.py`

NEW: now runs under self._client_lock -- serializes against a
        concurrently running replace_token(). Deliberate tradeoff: if
        replace_token() is currently active, this call waits until it's
        done, instead of accessing a half-disconnected client -- in the
        worst case this can extend the response time by the duration of
        a token swap, never by more than `timeout` itself.

        NEW (this session, prompted by a real field report): reconnects
        first if the connection is currently known to be down.
        Previously, any caller doing a plain sequential series of
        get_shadow() calls with no reconnect logic of its own (e.g.
        verify_named_shadows.py's simple loop, unlike watch_state()/
        watch_mission_timeline()'s own hardened _watch_topic()) would,
        after a single silent mid-run disconnect, have EVERY subsequent
        get_shadow() call in that run keep trying to subscribe/publish
        on a dead connection and time out -- matching a real report of
        "first N shadows succeed, every one after that fails" with N
        varying between runs (consistent with a disconnect landing at
        an unpredictable point in the sequence, not a fixed request-
        count limit). This matches a known, documented AWS IoT MQTT SDK
        behavior: after a session is lost (a broker-side session
        timeout, or the connection dropping for long enough), the
        broker forgets prior subscriptions, and a client that doesn't
        proactively reconnect/resubscribe before its next operation
        will simply never receive a response, silently -- see e.g.
        aws/aws-iot-device-sdk-js-v2#117, where a field report
        (unrelated project, same underlying AWS IoT behavior) describes
        this exact symptom for shadow topics specifically. Cheap when
        already connected (self._connected is checked first, no-op in
        the common case) -- only pays the reconnect cost when actually
        needed.


## prime_robot.get_named_shadow

`roombapy_prime/prime_robot.py`

WHY THIS MATTERS (context from that analysis): the real app
        subscribes to a wildcard covering every named shadow
        ("/things/{blid}/shadow/name/+/get/accepted" and the "update/
        accepted" sibling), and five named shadows are known to exist
        from that pattern -- but this library has only ever queried two
        of them (classic + "rw-settings"). The other three --
        "rw-constatus", "rw-schedule", "rw-software" -- have never been
        queried. "rw-constatus" is a strong candidate for where
        battery/charging status might live (plausibly short for
        "connection status"), given RobotStatusV2's own confirmed value
        is derived in the native app from FOUR combined streams
        (MissionData/SettingsData/AssetNetworkData/OTAStatusData) via
        rxcpp::combine_latest, not received as one ready-made field --
        meaning it's very plausibly assembled from more shadows than
        the two already queried. A specific EARLIER mistake, worth
        remembering: "rw-constatus" was previously written off because
        the app's own command config only lists a write-side
        SetEchoCommand (read: false) for it -- but that config
        describes COMMANDS, not SUBSCRIPTIONS; the wildcard subscribes
        to a named shadow regardless of whether any explicit read
        command exists for it. That distinction is exactly what this
        method exists to let someone check.

        Purely a read -- no different in risk from get_state()/
        get_settings(), which already do the same underlying MQTT
        request/response exchange against a different name.


## prime_robot.trigger_echo_via_shadow

`roombapy_prime/prime_robot.py`

Originally a hypothesis prompted by a real bug report: a field
        tester found ha_roomba_plus's existing locate action --
        poll_echo_value(), a REST POST to /v1/robots/{blid}/echo --
        does NOT actually make the robot chime, even though the same
        action works from the real app. ConnectionStatusShadow's
        "echo" field was noted to plausibly correspond to the app's
        own "SetEchoCommand" -- the exact command name the "find my
        robot" feature is built on, per the app's command config --
        making a shadow write, rather than a REST call, seem like a
        promising alternative mechanism.

        ACTUAL TEST RESULT: calling this with value=True produced a
        genuine, accepted shadow write -- confirmed by a real
        update/delta response (ShadowResponse with a real version
        number, "state": {"echo": True}). The write mechanism itself
        works correctly. But the robot did NOT chime, and "locate"
        from the real app worked fine on the same device immediately
        after -- confirming the ROBOT's own locate feature is not
        broken, only this particular guess at how to trigger it
        remotely. A delta response specifically (not just
        update/accepted) means a listening device would normally see
        this as "something changed that I should act on" -- yet
        nothing observable happened, suggesting either the robot
        doesn't actually watch this specific field for this purpose,
        or "SetEchoCommand" refers to something else entirely (e.g. a
        connectivity heartbeat/ping, consistent with rw-constatus
        otherwise being about network connection status, not
        chime-related at all).

        STILL UNRESOLVED: the actual "find my robot" trigger mechanism
        for Prime/V4 robots. Kept as a library method since the
        underlying write mechanism (arbitrary rw-constatus field
        writes) may still be useful for other, unrelated
        investigation, not because this specific use case works.


## prime_robot.send_mission_command

`roombapy_prime/prime_robot.py`

Originally CONFIRMED (session 15) via
        base_roomba_config.json's "Control" commandId entry:

            {"commandId": "Control", "topic": "cmd", "namedShadow": ""}

        MISREADING CORRECTED (session 39): "namedShadow": "" was read
        as "classic (unnamed) shadow, therefore send via
        $aws/things/{blid}/shadow/update" -- but cross-referencing the
        "topic" field across ALL 77 commandIds in the same file (not
        just this one entry in isolation) shows "topic" is itself a
        discriminator with (at least) three distinct categories:
        "shadow" (2 commandIds, incl. GetThingShadow -- confirmed live
        as get_state()'s classic shadow GET), "delta" (57 commandIds,
        all settings/schedule-style writes -- confirmed live as
        update_shadow()'s desired-state mechanism), and "cmd" (4
        commandIds: Control, AssetControlCommand, ResetRobotCommand,
        StartMatterCommissioning). "cmd" being its own category,
        distinct from both "shadow" and "delta", was the clue that got
        missed the first time -- mission commands were never meant to
        go through the shadow /update mechanism at all. "namedShadow":
        "" for a "cmd"-category entry doesn't mean "classic shadow";
        it's presumably just not applicable to this category.

        A live test (chairstacker, session 39) confirms this
        practically: every attempt via this method (update_shadow())
        timed out with ZERO response -- not even /rejected, which is
        consistent with publishing to a topic the AWS IoT shadow
        service doesn't recognize as a shadow topic at all, not a
        payload or permission problem on an otherwise-valid one.


## prime_robot.send_simple_command

`roombapy_prime/prime_robot.py`

`command` is a plain string, not MissionCommandType -- the
        confirmed-LIVE verb set (start, pause, stop, resume, dock,
        find) is narrower than this library's own 30-value enum (evac,
        reset, StartOnDemandOta, and more) -- pass MissionCommandType
        values for enum safety, or a plain string for anything not yet
        in the enum.

        CONFIRMED WORKING (jayjay, real device test): sending "find"
        produced a genuine, audible chime with no robot movement --
        exactly the expected find-my-robot behavior. This is the
        RESOLUTION of this whole project's locate-mechanism search --
        the two earlier attempts (a REST endpoint, a shadow write; see
        trigger_echo_via_shadow()'s own docstring) were both tried
        live and confirmed NOT working; this third, distinct transport
        (send_simple_command's own cmd-topic channel, not another
        shadow write) is the one that actually works.

        A separate native-analysis track had already traced the real
        app's locate button through
        MissionUIServiceCommand.FindLocateRobotRunAction to a
        CommandType enum value named FIND (Kotlin constant name,
        uppercase, from liblegacyCore.so's own string table) --
        MissionCommandType.FIND above IS this exact same enum
        (com.irobot.data.missioncommand.datamodels.CommandType), and
        its confirmed @SerialName wire value is the lowercase "find"
        already listed -- that reasoning is what predicted this result
        correctly, now confirmed live rather than just plausible.

        A second candidate from that same analysis, "FBEEP" (also
        found in liblegacyCore.so, right next to FIND) is NOT part of
        this project's own confirmed CommandType enum at all --
        "liblegacyCore" in its own filename raised a real question
        about whether it even applies to Prime robots' command channel
        the way FIND does -- moot now that FIND itself is confirmed
        working, no fallback needed.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult),
        same requirement as watch_live_map() -- raises RuntimeError
        immediately if missing, rather than silently publishing to a
        malformed topic.

        NOT region-aware -- there is no known way to specify
        rooms/zones/CommandParams through this simple payload shape.
        For that, RoutineCommand/send_mission_command() may still be
        the right (if unconfirmed) tool -- or an entirely different,
        not-yet-discovered mechanism may be needed. Fire-and-forget, no
        response wait -- see publish_cmd()'s docstring for why.

        CONFIRMED STRUCTURAL LIMITATION (parallel native-analysis
        track): the real app's own basic "Start" button does NOT send
        a bare command the way this method does -- it explicitly
        fetches the account's currently active cleaning preferences
        (suction level, carpet boost mode, etc.) and sends a full
        CommandParams built from them (see CommandParams's own
        docstring for the confirmed evidence trail). This method's
        payload shape structurally cannot carry any of that -- not a
        missing optional field, a fundamentally simpler wire shape
        than RoutineCommand. Whatever suction/carpet-boost setting the
        robot ends up running a mission with, when started this way,
        is decided entirely by the robot's own fallback, not by
        mirroring the account's actual saved preferences -- a real
        parity gap with the app's own behavior, worth knowing if a
        mission behaves differently (e.g. runs at unexpectedly high
        power) than starting the same robot from the real app would.


## prime_robot.send_routine_command_via_cmd_topic

`roombapy_prime/prime_robot.py`

THE HYPOTHESIS: send_simple_command()'s confirmed-working
        payload ({"command": str, "time": int, "initiator": str}) and
        RoutineCommand.to_json()'s own, independently-confirmed field
        mapping (see models/mission_control.py's RoutineCommand docstring, confirmed
        via @SerialName annotations in the decompiled source) share
        TWO exact key names: "command" (from RoutineCommand.type) and
        "initiator" (RoutineCommand's own confirmed field). This is
        not likely to be coincidence -- it suggests cmd_topic() may
        accept RoutineCommand's fuller structure (region_id/params/
        p2map_id/favorite_id and the rest), with "time" added on top,
        rather than being a fundamentally different, unrelated schema
        that happens to share two names.

        WHAT THIS METHOD DOES: publishes `command.to_json()` merged
        with a "time" field to cmd_topic(), via
        mqtt_client.py's publish_cmd_payload(). Nothing more.

        WHY THIS HAS NOT BEEN LIVE-TESTED: unlike the original
        transport question (where a wrong guess just produced silence,
        confirmed safe), a wrong guess HERE could mean a real device
        accepts a malformed but plausible-looking command and behaves
        unpredictably -- not zero risk, unlike the topic-discovery
        problem this hypothesis descends from.

        CORRECTED (this session, parallel native-analysis track,
        directly reversing an earlier recommendation here): the
        earlier advice was to favor a favorite_id-ONLY RoutineCommand
        over hand-built regions, reasoning that referencing something
        already app-defined would be safer. That's now known to be
        WRONG -- traced directly through the real app's own
        RoutineCommandBuilder: setFromFavorite(favoriteId, commandDefs)
        stores BOTH the favorite_id AND the favorite's full, resolved
        command definitions (regions/params/id_multipolys/map_id/
        pmap_version_id), and build() sends ALL of it together, not
        favorite_id alone. A favorite_id-only command is not a safer
        subset of what the app does -- it's something the app itself

        GAP CLOSED (roombapy-prime v0.1.11a21): this finding sat
        documented but unimplemented for a while -- verify_region_
        commands.py's own stages 1/1b/2 never actually added
        favorite_id to the outgoing command, despite fetching the
        favorite (and therefore knowing its real id) every time. Every
        real field-test payload up to that point (chairstacker,
        jayjay13011) was missing this field entirely. Now fixed via
        _add_favorite_id_if_missing() -- see that function's own
        docstring in verify_region_commands.py.
        never actually sends, and deviating from confirmed real
        behavior is the greater risk here, not the lesser one.

        UPDATE (same track, follow-up): build() also computes a
        routine_modified flag by comparing the command being built
        against the ORIGINAL favorite (region count, region order/IDs,
        and each region's user-modifiable params -- see CommandParams'
        own docstring for the exact 7-field non-user-modifiable list).
        This is a COMPUTED value, not something to set arbitrarily --
        which means hand-constructing a "favorite_id + resolved
        regions" command from scratch would ALSO need this comparison
        done correctly to match real behavior.

        UPDATE (same track, ad-hoc zones specifically): a hand-built
        TID (ad-hoc/temporary zone) region is a further, separate risk
        on top of the above -- its id must come from a reserved
        160-199 range (a real device manages this via its own
        adHocCounter, not something to invent), and its paired
        CommandPolygon must share that exact same id, with metadata
        referencing a real furniture id. RID/ZID (persistent rooms/
        zones from actual map data) don't have this extra constraint.
        See RegionType.TID's own docstring for the full mechanism.

        THE ACTUAL SAFEST TEST, given all of this: don't hand-
        construct anything, and avoid TID/ad-hoc regions entirely.
        Fetch an existing favorite via get_favorites(), take one of
        its own command_defs entries completely UNCHANGED (ideally
        one using ordinary RID/ZID regions from real map data, not an
        ad-hoc one), and send exactly that via this method. Since
        nothing was modified relative to its own origin, whatever
        routine_modified value it already carries (likely False/
        absent, as an unmodified replay) should already be correct --
        this sidesteps both the modified-flag computation question and
        the ad-hoc-region-construction question entirely, rather than
        needing to get either right from scratch.

        Same requirements/behavior as send_simple_command(): needs
        irbt_topic_prefix, fire-and-forget (no response wait, see
        publish_cmd()'s docstring for why).

        FIRST LIVE TEST RESULT (chairstacker, real device): the
        actual safest test described above -- an existing favorite's
        own command_def, resent completely unchanged -- produced NO
        observable effect. The robot did not move, and nothing
        appeared in the real app either. Cause not yet isolated
        between two real possibilities: (a) the favorite's own map/
        zone reference (p2map_id + user_p2mapv_id) may simply be
        stale if the map has been rebuilt since the favorite was
        created -- a robot-side rejection of an outdated reference
        that would happen regardless of transport, or (b) the
        transport hypothesis itself (this method existing at all) may
        be wrong. Distinguishing test in progress: whether the exact
        same favorite still works when run from the real app. Treat
        this method as still fully unconfirmed either way -- this
        result doesn't newly confirm OR rule out the hypothesis by
        itself.


## prime_robot.send_umi_get_request

`roombapy_prime/prime_robot.py`

UPDATE (this session, live wildcard capture, chairstacker): a
        live mission was captured with THIS exact request sent, and
        separately, "pos_update" messages containing what looks like
        live position/path data were ALSO observed arriving on the
        wildcard channel -- repeatedly, throughout the mission. CHECKED
        DIRECTLY, not just assumed: the FIRST pos_update in that
        capture (timestamp 1784491542) arrived 8 seconds BEFORE this
        exact request was sent (its own echoed "time" field:
        1784491550) -- pos_update was already flowing before the
        request existed, which settles it: this is not a response to
        this request, position data is simply pushed continuously
        regardless (see mqtt_client.py's notes next to
        rejected_report_topic() for the full pos_update finding).
        UPDATED again (jayjay13011, v0.1.11a6): the exact topic is now
        confirmed too (livemap_topic()), and watch_live_map() is the
        proper, already-built, now-also-confirmed way to consume this
        -- no request needed, and no need to fall back to a generic
        wildcard capture either. Left in place since the request itself
        was still a reasonable thing to have tried, and this doesn't
        rule out this request mattering for some other purpose (args
        other than "pose"?) --
        but "pose" specifically no longer looks like it needs this path.

        THE HYPOTHESIS: a request payload for the legacy "UMI" protocol
        family was found as a literal string in libcorebase.so:
        {"do": "get", "args": ["pose"], "id": <n>} -- alongside a
        general write-side pattern {"do": "set", "args": [%s]}. This is
        a generic do/args/id request protocol, not tied to a specific
        topic path -- which also explains why no dedicated
        "/things/%s/position"-style topic literal could be found at
        all (see mqtt_client.py's notes next to rejected_report_topic()
        for the full investigation trail): the intent lives in the
        payload (args=["pose"]), not in the topic.

        WHAT THIS METHOD DOES: publishes {"do": "get", "args": args,
        "id": request_id} to cmd_topic() -- the SAME topic
        send_simple_command() already uses, confirmed working for its
        own (differently-shaped) payload. Nothing more; this does not
        wait for or know where a response would arrive.

        WHY THE RESPONSE SIDE IS ESPECIALLY UNCERTAIN: a separate
        finding, core::RoombaSchemaField::kRobotPositionResponseTopic,
        suggests the response topic may be specified BY the requester
        inside the request payload itself, rather than being a fixed,
        predictable path -- meaning even a successful request might
        not have anywhere obvious to listen for the answer. A wildcard
        subscription (see watch_raw_topic(), or
        verify_mission_timeline.py's --watch-wildcard) is the practical
        way to have any chance of catching a response, since its
        destination can't be predicted in advance.

        WHY THIS HAS NOT BEEN LIVE-TESTED, AND THE CAVEAT THAT MATTERS
        MOST: this exact do/args/id literal was found associated with
        the UMI/legacy protocol family, which a related investigation
        confirmed has AT LEAST ONE transport variant that is local-
        HTTPS-only, not cloud-reachable at all (see
        GetAssetMissionStatusCommand's notes in mqtt_client.py). UMI
        does have other, MQTT-capable variants too (confirmed by a
        "Could not parse mqtt umi pose response" error string), so
        this is not automatically a dead end -- but whether THIS
        specific request, sent to THIS specific topic (cmd_topic, the
        AWS IoT command channel), is one of the MQTT-capable variants
        or the local-only kind is genuinely unknown. Same elevated-
        risk caveat as send_routine_command_via_cmd_topic(): a wrong
        guess here means a real device receiving a plausible-looking
        but not-actually-matching request, not the safe silence a
        topic-discovery mismatch would produce.

        Same requirements as the other cmd_topic()-based methods: needs
        irbt_topic_prefix, fire-and-forget.


## prime_robot.watch_state

`roombapy_prime/prime_robot.py`

named=None -> classic shadow delta (works on both tiers tested
        so far). named="rw-settings" -> named shadow delta, expected to
        only work on SMART tier -- on EPHEMERAL, this iterator then
        presumably never delivers anything (no error, just silence,
        analogous to get_shadow()'s timeout behavior -- but there's no
        timeout here, since "wait for the next change" is the whole
        point).

        IMPORTANT (this session): a live idle-vs-mid-mission diff of
        get_state() (chairstacker) confirmed the classic shadow's
        reported state is BYTE-IDENTICAL whether the robot is idle or
        actively cleaning -- but that's a snapshot comparison (two
        separate GET requests), not a test of this method itself.

        CORRECTION (this session, parallel reverse-engineering track):
        this docstring previously claimed live mission status "does NOT
        flow through get_state()/watch_state() at all" -- the
        watch_state() part of that claim was an unverified extension of
        the get_state() snapshot-diff finding, never actually tested.
        This method's own delta subscription has never been run live
        during an active mission. It remains a real, concrete,
        not-yet-run test: AWS IoT's standard shadow/update/delta
        semantics push a message on every change, which a before/after
        snapshot comparison could simply never surface even if changes
        genuinely happen in between. Kept for whatever it DOES cover
        (map/settings-adjacent changes) -- but "kept for" no longer
        means "confirmed to be useless for mission/battery status
        specifically"; that's now an open question again, not a closed
        one.

        queue_maxsize: bounds the internal buffer (see
        DEFAULT_WATCH_QUEUE_MAXSIZE). When the buffer is full, the
        OLDEST entry is dropped (not the newest) -- a lagging consumer
        this way gets the most current state, not the longest queue.
        Every drop is logged as a WARNING.

        IMPORTANT: the delta topic itself (.../update/delta) is part of
        AWS IoT's standard shadow behavior (delivers a message
        immediately upon subscribing if desired/reported differ, then
        on every subsequent change) -- this standard semantic is
        assumed here, not specifically verified for Classic/Prime.

        RECONNECTS TRANSPARENTLY, unbounded retries with exponential
        backoff -- see _watch_topic()'s own docstring, which does the
        actual work here; this method's only job is picking the topic.


## prime_robot.watch_mission_timeline

`roombapy_prime/prime_robot.py`

Subscribes to {irbt_prefix}/things/{blid}/mission/timeline/report,
        found via native decompilation (libcorebase.so's
        core::protocol::AssetIotTopicFactory::createMissionTimelineTopic(),
        prompted by a live finding: two separate get_state() snapshots
        (idle vs. mid-mission) were byte-identical.

        CORRECTION (this session, parallel reverse-engineering track):
        the original framing here overreached. What was actually tested
        is a snapshot DIFF of get_state() -- two point-in-time GET
        requests, compared. watch_state()'s own delta subscription
        (.../shadow/update/delta, AWS IoT's standard push-on-change
        mechanism) has never actually been run live WHILE a mission was
        active -- only assumed, by extension, to behave the same way.
        That assumption was never tested and may be wrong: a delta
        subscription could plausibly see intermediate changes a
        before/after snapshot comparison would never surface. See
        watch_state()'s own docstring for the correction there too.
        This topic (mission/timeline/report) remains a solid, separately
        justified finding either way -- it doesn't depend on the
        watch_state() question.

        WHAT'S CONFIRMED vs. NOT, precisely:
        - The topic NAME and its existence: confirmed, from native
          symbols (createMissionTimelineTopic, IotTopicType::kReport).
        - The irbt_topic_prefix applying here the same way it does for
          the already-live-confirmed command topic
          (createCommandPublishTopic, same factory, same constructor):
          now CONFIRMED (parallel APK-research chat, this session) via
          decompiled call-site code for all three of
          AssetIotTopicFactory's topic-building methods -- same stored
          constructor value, same concatenation pattern, no structural
          difference from the live-confirmed command topic.
        - The payload SHAPE: genuinely unknown. RobotMissionStatusEventImpl's
          decompiled constructor signature (AssetId, RobotMissionType,
          string, RobotMissionPhase, string, short, short, int,
          RobotReadinessState, short, vector<RobotReadinessState>,
          vector<short>, short, int, long, long, long, string,
          optional<int>) suggests real mission fields exist somewhere
          in whatever arrives here, but there is no confirmed JSON
          mapping for any of it -- this method exists to capture a live
          sample, not to parse one. ShadowResponse.payload is whatever
          JSON (or raw string, if not JSON) arrives, completely
          unparsed/untyped.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult) --
        raises ValueError immediately if not available, same as
        send_simple_command()/watch_live_map().

        Same reconnect-with-backoff behavior as watch_state() -- see
        _watch_topic()'s docstring.


## prime_robot.watch_named_shadows_updates

`roombapy_prime/prime_robot.py`

WHY update/accepted, not update/delta: delta only reflects
        differences between desired and reported state -- fields that
        are purely device-reported (never written as "desired", e.g.
        a battery percentage) never appear in a delta message no
        matter how often they change, confirmed directly from AWS's
        own Device Shadow documentation. update/accepted fires on
        every accepted shadow update regardless of desired/reported
        matching, making it the correct channel for read-only,
        report-only shadow content like ro-currentstate's battery/
        dock/bin fields.

        Each yielded ShadowResponse's own `.topic` tells you which
        named shadow the update came from (the wildcard resolves to
        the real shadow name in the actual message) -- parse the
        segment between ".../shadow/name/" and "/update/accepted" if
        you need to distinguish them.

        NOT YET LIVE-TESTED as of this writing -- a reasoned, safety-
        checked hypothesis (matching a confirmed real-app pattern),
        not a confirmed-working mechanism yet.


## prime_robot.watch_live_map

`roombapy_prime/prime_robot.py`

CORRECTED (July 11, see
        docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md point B1) -- an
        earlier version called get_live_map_stream() and subscribed to
        the topic returned in it. That was a misunderstanding: in the
        real app (P2MapAPIFetching.observeLiveMap()), a FIXED topic is
        subscribed to (see mqtt_client.py's livemap_topic()), and
        get_live_map_stream() only keeps running as a periodic
        keep-alive in the background, for as long as it's being
        watched.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult)
        -- if that's None (field name from the discovery response not
        confirmed, see there), this method immediately raises a
        RuntimeError, instead of silently waiting on an incorrectly
        constructed topic.

        keep_alive_interval: how often the keep-alive ping is sent
        while watching. The real app uses a more complex scheme
        (timer relative to an expiration/refreshWindowMillis, see
        LiveMapKeepAlivePublisher) -- deliberately simplified here to
        a fixed interval, since the original's exact lookup/trigger
        logic wasn't fully reconstructed. If a single keep-alive ping
        fails, this is logged as a WARNING, but watching continues (a
        ping failure shouldn't abort the whole watcher).

        queue_maxsize: see watch_state() -- same drop-oldest
        backpressure policy. IMPORTANT LIMITATION here: errors (see
        next paragraph) go through the same queue as normal messages
        and are therefore NOT exempt from the drop-oldest policy -- an
        error could theoretically be dropped if the queue happens to
        be full when it arrives. An accepted limitation for this
        draft, no special case built in for errors.

        Messages of unknown shape (neither pos_update nor map_update,
        see parse_livemap_message_data) are NOT silently skipped -- the
        error propagates through the generator, the caller sees it on
        the next `async for` step. This is a deliberate choice: an
        unknown message format on a channel that's never been tested
        live is something that should stand out, not something to
        silently discard.


## rest_client.get_map_geojson_link

`roombapy_prime/rest_client.py`

GET /v1/p2maps/{map_id}/versions/{map_version}/geojson
                ?response_type=link

        Confirmed from P2MapGeoJSONRequest.java: `response_type` is an
        enum with @SerialName("link")/@SerialName("binary") -- "link"
        (the default in the original) requests a presigned download URL
        (Accept: application/json, which happens to match the default
        header already set anyway). "binary" (direct gzip, Accept:
        application/gzip,application/json) is NOT supported here --
        would need a parametrizable Accept header, which aws_sigv4.py
        doesn't currently offer.

        CORRECTED (session 48): the response shape (which JSON key
        carries the actual URL) is now confirmed via
        P2MapURL$$serializer's <clinit> -- the key is `map_url`.
        Previously marked entirely unconfirmed ("no dedicated response
        class found") -- that class does exist
        (com.irobot.irobotdata.maps.internal.p2maps.editing.common.
        responses.P2MapURL), it just wasn't found in the earlier
        source-code search. Still returned as raw JSON here (not worth
        a dedicated dataclass for a single field), but callers can now
        reliably do `result["map_url"]` instead of guessing among
        candidate keys.

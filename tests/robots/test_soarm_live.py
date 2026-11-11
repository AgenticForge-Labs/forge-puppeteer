"""SO-ARM live presence: motion generator, HTTP client against a fake SDK, and config."""
import itertools
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest

from forge_puppeteer.devices.soarm101_trusted_http import (
    SOARM101TrustedChoreographyHTTP,
)
from forge_puppeteer.execution import ActionRefused, ActionRequest
from forge_puppeteer.soarm_live import LivePresence, LivePresenceSettings

REV = "sha256:" + "c" * 64
DEG = math.radians(1.0)
LIMITS = tuple(6 * DEG if i else 0.0 for i in range(5))
SETTINGS = LivePresenceSettings(
    "presence", REV, (0.0, 3 * DEG, -2 * DEG, 0.0, 0.0), LIMITS)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def run(self, presence, seconds, step=0.05):
        out = []
        for _ in range(round(seconds / step)):
            self.now += step
            out.append(presence.offsets())
        return out


class Speech:
    def __init__(self, values):
        self.values = values

    def latest(self):
        return self.values

    def advance(self, **changes):
        """The next hop arrives."""
        self.values = {**self.values, "sequence": self.values.get("sequence", 0) + 1, **changes}


def make():
    clock = Clock()
    return LivePresence(SETTINGS, clock=clock), clock


def column(samples, joint):
    return [s[joint] for s in samples]


def test_breathing_is_continuous_and_stays_in_the_pinned_range():
    presence, clock = make()
    samples = clock.run(presence, 12)
    lift = column(samples, 1)
    assert max(lift) == pytest.approx(3 * DEG, abs=0.15 * DEG) and min(lift) >= -1e-9
    assert max(abs(b - a) for a, b in itertools.pairwise(lift)) < 0.6 * DEG, "no jumps between samples"
    for sample in samples:
        assert all(abs(v) <= limit + 1e-12 for v, limit in zip(sample, LIMITS))
    assert not any(column(samples, 3)) and not any(column(samples, 4)), "no talking while idle"


def test_listening_settles_to_the_centre_and_breathing_resumes_smoothly():
    presence, clock = make()
    clock.run(presence, 5)
    presence.set_behavior("listening")
    held = clock.run(presence, 2)
    assert max(abs(v) for v in held[-1]) < 1e-9
    assert max(abs(b - a) for a, b in zip(column(held, 1), column(held, 1)[1:])) < 0.5 * DEG
    presence.set_behavior("idle")
    back = clock.run(presence, 3)
    assert max(abs(b - a) for a, b in zip(column(back, 1), column(back, 1)[1:])) < 0.5 * DEG
    assert max(column(back, 1)) > 1 * DEG


def test_talking_follows_the_speech_level_and_goes_quiet_in_silence():
    presence, clock = make()
    speech = Speech({"active": True, "mouth": 0.6, "stream_id": "s", "sequence": 1})
    presence.speech = speech
    presence.set_behavior("speaking")
    loud = []
    for _ in range(120):  # hops keep arriving while the voice is on
        speech.advance()
        clock.now += 0.05
        loud.append(presence.offsets())
    flex = column(loud[40:], 3)
    assert max(flex) == pytest.approx(5 * DEG, abs=0.5 * DEG) and min(flex) == pytest.approx(-5 * DEG, abs=0.5 * DEG)
    assert presence.source == "speech"
    speech.advance(active=False, mouth=0.0)
    quiet = clock.run(presence, 3)
    assert max(abs(v) for v in column(quiet[-10:], 3)) < 0.2 * DEG
    soft = []
    for _ in range(80):
        speech.advance(active=True, mouth=0.1)
        clock.now += 0.05
        soft.append(presence.offsets()[3])
    soft = soft[30:]
    assert 0.5 * DEG < max(soft) < 3 * DEG, "a quiet voice nods less than a loud one"


def test_talking_keeps_a_gentle_rhythm_when_no_features_arrive():
    presence, clock = make()
    presence.set_behavior("speaking")
    samples = clock.run(presence, 5)
    assert presence.source == "fallback"
    assert 1 * DEG < max(column(samples[30:], 3)) < 4 * DEG


def test_talking_never_leaves_the_envelope_and_never_jerks():
    presence, clock = make()
    presence.speech = Speech({"active": True, "mouth": 1.0})
    presence.set_behavior("speaking")
    samples = clock.run(presence, 8)
    for sample in samples:
        assert all(abs(v) <= limit + 1e-12 for v, limit in zip(sample, LIMITS))
    flex = column(samples, 3)
    peak_speed = max(abs(b - a) for a, b in itertools.pairwise(flex)) / 0.05
    assert math.degrees(peak_speed) < 45, "slow enough for the SDK limits"


def test_settings_refuse_motion_outside_the_pinned_range():
    with pytest.raises(ValueError, match="exceeds the pinned envelope"):
        LivePresenceSettings("presence", REV, (0, 7 * DEG, 0, 0, 0), LIMITS)
    with pytest.raises(ValueError, match="exceeds the pinned envelope"):
        LivePresenceSettings("presence", REV, (0,) * 5, (0, 0, 0, 4 * DEG, 4 * DEG))


class FakeSDK:
    """Loopback stand-in for the SDK's /v1/trusted/stream routes."""

    def __init__(self, *, end_after=None, refuse_open=None):
        self.calls = []
        self.end_after = end_after
        self.refuse_open = refuse_open
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                route = self.path.rsplit("/", 1)[-1]
                outer.calls.append((route, body))
                status, payload = 200, {"ok": True, "result": {"active": True}}
                if route == "open" and outer.refuse_open:
                    status, payload = 409, {"ok": False, "error": outer.refuse_open}
                if route == "offsets" and outer.end_after is not None and \
                        sum(c[0] == "offsets" for c in outer.calls) > outer.end_after:
                    status, payload = 409, {"ok": False, "error": "live stream is not active"}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


def live_request(presence):
    return ActionRequest(
        id="a:live:0", performer_id="arm", caller_id="c", session_id="s", operation="live_presence",
        parameters={}, cue_id="live:0", asset_id=presence.asset_id, asset_revision=presence.asset_revision)


def client(sdk, presence):
    return SOARM101TrustedChoreographyHTTP(sdk.url, "tok", {}, live_presence=presence)


@pytest.mark.anyio
async def test_the_stream_opens_sends_offsets_and_closes_when_asked():
    sdk = FakeSDK()
    presence, _ = make()
    presence._clock = __import__("time").monotonic
    link = client(sdk, presence)

    async def finish_later():
        await anyio.sleep(0.4)
        presence.close()

    async with anyio.create_task_group() as tg:
        tg.start_soon(finish_later)
        completion = await link.execute(live_request(presence))
    routes = [c[0] for c in sdk.calls]
    assert routes[0] == "open" and routes[-1] == "close" and routes.count("offsets") >= 4
    assert sdk.calls[0][1]["name"] == "presence" and sdk.calls[0][1]["revision"] == REV
    assert all(len(c[1]["offsets_rad"]) == 5 for c in sdk.calls if c[0] == "offsets")
    assert "closed" in completion.evidence


@pytest.mark.anyio
async def test_a_stream_the_sdk_ended_completes_without_a_close():
    sdk = FakeSDK(end_after=3)
    presence, _ = make()
    presence._clock = __import__("time").monotonic
    await client(sdk, presence).execute(live_request(presence))
    assert "close" not in [c[0] for c in sdk.calls]


@pytest.mark.anyio
async def test_a_refused_open_is_a_definite_refusal():
    sdk = FakeSDK(refuse_open="measured start is not at the pinned envelope centre")
    presence, _ = make()
    with pytest.raises(ActionRefused):
        await client(sdk, presence).execute(live_request(presence))
    assert [c[0] for c in sdk.calls] == ["open"]


@pytest.mark.anyio
async def test_a_request_for_a_different_envelope_is_never_sent():
    sdk = FakeSDK()
    presence, _ = make()
    request = live_request(presence).model_copy(update={"asset_revision": "sha256:" + "d" * 64})
    with pytest.raises(PermissionError):
        await client(sdk, presence).execute(request)
    assert sdk.calls == []


def test_talking_stops_when_the_speech_features_end_instead_of_falling_back_to_nodding():
    presence, clock = make()
    speech = Speech({"active": True, "mouth": 0.6})
    presence.speech = speech
    presence.set_behavior("speaking")
    clock.run(presence, 3)
    assert presence.source == "speech"
    speech.values = None  # the features stop arriving: the generated audio has ended
    tail = clock.run(presence, 1.0)
    assert presence.source == "ended"
    assert max(abs(v) for v in column(tail[-8:], 3)) < 0.3 * DEG, "quiet well within a second"


def test_talking_waits_briefly_for_features_then_falls_back_only_if_none_come():
    presence, clock = make()
    presence.speech = Speech(None)
    presence.set_behavior("speaking")
    early = clock.run(presence, 0.4)
    assert presence.source == "waiting" and not any(column(early, 3))
    clock.run(presence, 1.5)
    assert presence.source == "fallback"
    # A new turn starts the wait over, and features arriving end it.
    presence.set_behavior("listening")
    clock.run(presence, 1.0)
    presence.speech = Speech({"active": True, "mouth": 0.5})
    presence.set_behavior("speaking")
    clock.run(presence, 1.0)
    assert presence.source == "speech"


def test_leaving_speaking_fades_the_talking_out_quickly():
    presence, clock = make()
    presence.speech = Speech({"active": True, "mouth": 1.0})
    presence.set_behavior("speaking")
    clock.run(presence, 4)
    presence.set_behavior("listening")
    after = clock.run(presence, 0.5)
    assert max(abs(v) for v in column(after[-3:], 3)) < 0.5 * DEG


def test_features_that_stop_advancing_end_the_talking_quickly_without_waiting_for_staleness():
    presence, clock = make()
    speech = Speech({"active": True, "mouth": 0.6, "stream_id": "s", "sequence": 0})
    presence.speech = speech
    presence.set_behavior("speaking")
    for _ in range(60):  # three seconds of hops arriving every tick
        speech.advance()
        clock.now += 0.05
        presence.offsets()
    assert presence.source == "speech"
    held = clock.run(presence, 0.8)  # the last hop stays "latest" but never advances
    assert presence.source == "ended"
    assert max(abs(v) for v in column(held[-4:], 3)) < 0.5 * DEG
    assert max(abs(v) for v in column(held[:5], 3)) > 1 * DEG, "it did not cut off at once either"


class SettlingSDK:
    """Refuses a program with the 'not settled yet' text a few times, then runs it."""

    def __init__(self, refusals, text="healthy, connected and stationary SDK state required"):
        self.remaining, self.text, self.calls = refusals, text, 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                outer.calls += 1
                if outer.remaining > 0:
                    outer.remaining -= 1
                    status, payload = 409, {"ok": False, "error": outer.text}
                else:
                    status, payload = 200, {"ok": True, "result": {"revision": PIN}}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


PIN = "sha256:" + "e" * 64


def gesture_request():
    return ActionRequest(
        id="a:0:0", performer_id="arm", caller_id="c", session_id="s", operation="play_choreography",
        parameters={}, cue_id="yes:0:0", asset_id="soarm101/choreography/yes", asset_revision=PIN)


@pytest.mark.anyio
async def test_a_gesture_refused_only_because_the_arm_has_not_settled_is_retried_briefly(monkeypatch):
    import forge_puppeteer.devices.soarm101_trusted_http as module

    monkeypatch.setattr(module, "SETTLING_RETRY_S", 0.01)
    sdk = SettlingSDK(2)
    link = SOARM101TrustedChoreographyHTTP(sdk.url, "tok", {"yes": PIN}, durations_s={"yes": 3.0})
    completion = await link.execute(gesture_request())
    assert sdk.calls == 3 and "completed" in completion.evidence


@pytest.mark.anyio
async def test_other_refusals_are_not_retried_and_the_retry_is_bounded(monkeypatch):
    import forge_puppeteer.devices.soarm101_trusted_http as module

    monkeypatch.setattr(module, "SETTLING_RETRY_S", 0.01)
    other = SettlingSDK(5, text="active bound host watchdog required")
    link = SOARM101TrustedChoreographyHTTP(other.url, "tok", {"yes": PIN}, durations_s={"yes": 3.0})
    with pytest.raises(ActionRefused):
        await link.execute(gesture_request())
    assert other.calls == 1
    stuck = SettlingSDK(99)
    link = SOARM101TrustedChoreographyHTTP(stuck.url, "tok", {"yes": PIN}, durations_s={"yes": 3.0})
    with pytest.raises(ActionRefused):
        await link.execute(gesture_request())
    assert stuck.calls == module.SETTLING_RETRIES + 1


def test_each_turn_of_talking_leaves_a_summary_for_diagnosis():
    presence, clock = make()
    turns = []
    presence.turn_log = turns.append
    speech = Speech({"active": True, "mouth": 0.6, "stream_id": "s", "sequence": 0})
    presence.speech = speech
    presence.set_behavior("speaking")
    for _ in range(40):
        speech.advance()
        clock.now += 0.05
        presence.offsets()
    presence.set_behavior("listening")
    assert len(turns) == 1
    turn = turns[0]
    assert turn["ticks"]["speech"] > 30 and turn["max_flex_deg"] > 1.0
    assert turn["first_feature_s"] is not None and turn["first_motion_s"] is not None
    assert turn["feature_changes"] == 40 and turn["duration_s"] == pytest.approx(2.0, abs=0.1)
    # A turn with no motion at all is visible as such.
    silent, silent_clock = make()
    silent.turn_log = turns.append
    silent.speech = Speech({"active": False, "mouth": 0.0, "stream_id": "s", "sequence": 1})
    silent.set_behavior("speaking")
    silent_clock.run(silent, 1.0)
    silent.set_behavior("idle")
    assert turns[-1]["max_flex_deg"] < 0.5 and turns[-1]["first_motion_s"] is None

import anyio

from forge_puppeteer.actions import GestureAction, LookAtAction, SpeakAction
from forge_puppeteer.cameras import (
    AxisRange,
    CameraMotionCalibration,
    CameraMotionLimits,
    CameraMotionPosition,
    load_calibration,
    save_calibration,
)
from forge_puppeteer.embodiments import MockEmbodiment
from forge_puppeteer.performance import Cue, Performance
from forge_puppeteer.runtime import (
    Character,
    FakeClock,
    PerformanceStage,
    TimelineDirector,
)
from forge_puppeteer.voice import MockVoiceProvider


def test_timeline_executes_semantic_actions():
    embodiment=MockEmbodiment(); stage=PerformanceStage(FakeClock()); stage.register(Character('ember','Ember',embodiment)); perf=Performance(name='test',cues=(Cue(at=1,character='ember',action=GestureAction(name='wave')),Cue(at=0,character='ember',action=LookAtAction(target='camera'))))
    anyio.run(TimelineDirector(stage).perform,perf)
    assert [a.type for a in embodiment.actions]==['look_at','gesture']
    assert embodiment.stop_count==1


def test_speech_uses_explicit_voice_alias_and_holds_timeline_for_sample_duration():
    clock=FakeClock(); voice=MockVoiceProvider(); events=[]
    stage=PerformanceStage(clock); stage.emitter.subscribe(events.append)
    stage.register(Character('ember','Ember',MockEmbodiment(),voice))
    performance=Performance(name='speech',cues=(Cue(at=0,character='ember',action=SpeakAction(
        text='hello',audio_uri='sample://hello',voice_alias='warm',sample_duration=2.5,
    )),))

    anyio.run(TimelineDirector(stage).perform,performance)

    assert voice.requests[0]['voice_alias']=='warm'
    assert voice.requests[0]['audio_uri']=='sample://hello'
    speech_finished=next(event for event in events if event.type.value=='speech_finished')
    assert speech_finished.timestamp==2.5
    assert clock.now()==2.5


def test_legacy_speech_payload_and_text_only_provider_remain_supported():
    class LegacyVoice:
        def __init__(self):
            self.spoken = []

        async def speak(self, text):
            self.spoken.append(text)

        async def stop(self):
            pass

    action = SpeakAction.model_validate({"type": "speak", "text": "legacy"})
    voice = LegacyVoice()
    stage = PerformanceStage(FakeClock())
    stage.register(Character("ember", "Ember", MockEmbodiment(), voice))
    anyio.run(TimelineDirector(stage).perform,
              Performance(name="legacy", cues=(Cue(at=0, character="ember", action=action),)))
    assert action.voice_alias is None
    assert voice.spoken == ["legacy"]


def test_speech_failure_emits_failure_and_releases_voice_ownership():
    class FailingVoice(MockVoiceProvider):
        async def speak(self,text,**kwargs):
            raise RuntimeError('playback failed')

    voice=FailingVoice(); stage=PerformanceStage(FakeClock()); events=[]
    stage.emitter.subscribe(events.append)
    stage.register(Character('ember','Ember',MockEmbodiment(),voice))
    cue=Cue(at=0,character='ember',action=SpeakAction(text='hello',voice_alias='warm'))

    async def run():
        try:
            await stage.execute_cue('speech',cue)
        except RuntimeError:
            pass
        else:
            raise AssertionError('provider failure should propagate')
        # A second invocation must acquire the same voice resource after failure.
        voice.speak=MockVoiceProvider().speak
        await stage.execute_cue('speech',cue)

    anyio.run(run)
    assert sum(event.type.value=='action_failed' for event in events)==1
    assert sum(event.type.value=='action_completed' for event in events)==1


def test_overlapping_speech_cues_share_voice_ownership():
    class TrackingVoice(MockVoiceProvider):
        active=0; maximum_active=0
        async def speak(self,text,**kwargs):
            self.active+=1; self.maximum_active=max(self.maximum_active,self.active)
            await anyio.lowlevel.checkpoint()
            self.active-=1

    voice=TrackingVoice(); stage=PerformanceStage(FakeClock())
    stage.register(Character('ember','Ember',MockEmbodiment(),voice))
    performance=Performance(name='serialized-speech',cues=(
        Cue(at=0,character='ember',action=SpeakAction(text='first',voice_alias='warm')),
        Cue(at=0,character='ember',action=SpeakAction(text='second',voice_alias='warm')),
    ))

    anyio.run(TimelineDirector(stage).perform,performance)
    assert voice.maximum_active==1


def test_camera_calibration_round_trip(tmp_path):
    cal=CameraMotionCalibration('cam',CameraMotionPosition(0,0,.2),CameraMotionLimits(AxisRange(-1,1),AxisRange(-1,1),AxisRange(0,1)),{'close':CameraMotionPosition(.1,.2,.5)})
    path=tmp_path/'cal.json'; save_calibration(cal,path); loaded=load_calibration(path)
    assert loaded.controller_id=='cam'
    assert loaded.presets['close'].zoom==.5

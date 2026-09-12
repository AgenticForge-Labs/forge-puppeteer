import anyio
from forge_puppeteer.actions import GestureAction,LookAtAction
from forge_puppeteer.cameras import AxisRange,CameraMotionCalibration,CameraMotionLimits,CameraMotionPosition,load_calibration,save_calibration
from forge_puppeteer.embodiments import MockEmbodiment
from forge_puppeteer.performance import Cue,Performance
from forge_puppeteer.runtime import Character,FakeClock,PerformanceStage,TimelineDirector


def test_timeline_executes_semantic_actions():
    embodiment=MockEmbodiment(); stage=PerformanceStage(FakeClock()); stage.register(Character('ember','Ember',embodiment)); perf=Performance(name='test',cues=(Cue(at=1,character='ember',action=GestureAction(name='wave')),Cue(at=0,character='ember',action=LookAtAction(target='camera'))))
    anyio.run(TimelineDirector(stage).perform,perf)
    assert [a.type for a in embodiment.actions]==['look_at','gesture']
    assert embodiment.stop_count==1


def test_camera_calibration_round_trip(tmp_path):
    cal=CameraMotionCalibration('cam',CameraMotionPosition(0,0,.2),CameraMotionLimits(AxisRange(-1,1),AxisRange(-1,1),AxisRange(0,1)),{'close':CameraMotionPosition(.1,.2,.5)})
    path=tmp_path/'cal.json'; save_calibration(cal,path); loaded=load_calibration(path)
    assert loaded.controller_id=='cam'
    assert loaded.presets['close'].zoom==.5

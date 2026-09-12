from forge_puppeteer import PhysicalShotRequest, Puppeteer
from forge_puppeteer.stage import MockStage
from forge_puppeteer.performers import MockPerformer

def test_mock_execution(tmp_path):
    r=PhysicalShotRequest(production_id='p',episode_id='e',shot_id='s',duration_seconds=1,visual='wave')
    take=Puppeteer(MockStage(),[MockPerformer()]).execute(r,tmp_path)
    assert take.shot_id=='s'
    assert take.uri

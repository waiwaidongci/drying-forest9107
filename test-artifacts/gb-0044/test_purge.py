"""保密试件清除功能测试：追溯副本、状态机、并发防护、下载终止与恢复。"""
import os,sys,time,types,threading,tempfile
TMP=tempfile.mkdtemp(prefix='purge-test-')
os.environ['DATABASE_URL']='sqlite:///'+TMP+'/test.db'
os.environ['REDIS_URL']='redis://localhost:6379/15'
os.environ['JWT_SECRET']='purge-test-secret'
os.environ['ARTIFACT_DIR']=TMP+'/artifacts'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

import pytest
from fastapi.testclient import TestClient

PASSWORD='Research2026!'
EMAILS={r:r+'@drying-forest.demo' for r in ('admin','analyst','viewer')}

@pytest.fixture(scope='module')
def client():
    from src.config.database import Base,engine,Session
    from src.models import user,source,record,run,note,audit,purge
    Base.metadata.create_all(engine)
    from src.models.user import User
    from src.utils.security import hash_password
    with Session() as db:
        for role,email in EMAILS.items():
            db.add(User(email=email,password_hash=hash_password(PASSWORD),role=role))
        db.commit()
    from src.main import app
    with TestClient(app) as c:
        yield c

@pytest.fixture(autouse=True)
def fake_queue(monkeypatch):
    import src.services.runs as runs_svc
    pushed=[]
    monkeypatch.setattr(runs_svc,'queue',types.SimpleNamespace(rpush=lambda *a:pushed.append(a[1])))
    from src.services import downloads
    downloads.reset()
    return pushed

def token(client,role):
    r=client.post('/auth/login',json={'email':EMAILS[role],'password':PASSWORD})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def rec(ref,moisture=40.0,minutes=90.0):
    return {'reference':ref,'values':{'moisture':moisture,'thickness':4.0,'temperature':75.0,'airflow':3.0,'minutes':minutes}}

def make_source(client,head,name):
    r=client.post('/sources',json={'name':name,'filename':name+'.csv','description':'测试来源'},headers=head)
    assert r.status_code==200,r.text
    return r.json()['id']

def fill_records(client,head,sid,refs):
    r=client.post(f'/sources/{sid}/records',json={'records':refs},headers=head)
    assert r.status_code==200,r.text

def make_run(client,head,sid,name):
    r=client.post('/runs',json={'source_id':sid,'name':name,'parameters':{'trees':20,'depth':4}},headers=head)
    assert r.status_code==200,r.text
    return r.json()['id']

def finish_run(run_id):
    from src.worker import execute
    execute(run_id)

def test_purge_full_flow_with_inflight_download(client,monkeypatch):
    admin=token(client,'admin');analyst=token(client,'analyst')
    sid=make_source(client,analyst,'清除流程来源')
    refs=[rec(f'HX-A-{i:03}',moisture=30+i%20,minutes=80.0+i) for i in range(199)]
    refs.append(rec('HX-SEC-001',moisture=77.77,minutes=123.45))
    fill_records(client,analyst,sid,refs)
    run_id=make_run(client,analyst,sid,'清除流程研究')
    finish_run(run_id)
    detail=client.get(f'/runs/{run_id}',headers=admin).json()
    assert detail['state']=='completed'
    from src.services.purge import artifact_path
    assert artifact_path(run_id).exists()
    r=client.post('/notes',json={'run_id':run_id,'title':'清除流程备忘','body':'业务说明正文'},headers=analyst)
    assert r.status_code==200,r.text
    nid=r.json()['id']
    assert client.post(f'/notes/{nid}/approve',headers=admin).status_code==200
    # 在途下载：真实 HTTP 服务器（TestClient 会缓冲整个流，无法模拟传输中途），读到首块后发起清除
    import src.services.export as exp
    monkeypatch.setattr(exp,'CHUNK_PAUSE',0.08)
    import uvicorn,httpx
    from src.main import app
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18931,log_level='error'))
    st=threading.Thread(target=server.run,daemon=True);st.start()
    for _ in range(100):
        if server.started:break
        time.sleep(0.05)
    started=threading.Event();outcome={}
    def reader():
        try:
            with httpx.stream('GET',f'http://127.0.0.1:18931/runs/{run_id}/export',headers=admin) as resp:
                for _ in resp.iter_bytes():started.set()
            outcome['error']=None
        except Exception as e:
            outcome['error']=e
    t=threading.Thread(target=reader);t.start()
    try:
        assert started.wait(10),'下载未能开始'
        r=client.post('/purges',json={'reference':'HX-SEC-001','reason':'保密试件误混'},headers=admin)
        assert r.status_code==200,r.text
        purge=r.json()
        t.join(10)
        assert outcome.get('error') is not None,'已发起的下载必须在清除完成前被终止'
    finally:
        server.should_exit=True;st.join(10)
    assert purge['state']=='completed',purge
    locations={c['location'] for c in purge['copies']}
    assert {'source','snapshot','result','artifact','note'}<=locations
    assert all(c['state']=='cleared' for c in purge['copies'])
    # 来源：该试件消失，其余保留
    records=client.get(f'/sources/{sid}/records',headers=admin).json()
    assert len(records)==199 and all(x['reference']!='HX-SEC-001' for x in records)
    # 研究：失效说明无原始数值
    r=client.get(f'/runs/{run_id}',headers=admin)
    d=r.json()
    assert d['state']=='invalidated' and d['result'] is None
    assert '失效' in d['error']
    assert all(x['reference']!='HX-SEC-001' for x in d['snapshot']['records'])
    assert '77.77' not in r.text and '123.45' not in r.text
    assert client.get(f'/runs/{run_id}/export',headers=admin).status_code==409
    # 备忘：失效且不可下载
    notes=client.get('/notes',headers=admin).json()
    note=[n for n in notes if n['id']==nid][0]
    assert note['state']=='invalidated'
    assert 'notice' in note['frozen_result'] and 'rows' not in note['frozen_result']
    assert client.get(f'/notes/{nid}/export',headers=admin).status_code==403
    assert not artifact_path(run_id).exists()
    # 重复导入被拦截
    r=client.post(f'/sources/{sid}/records',json={'records':[rec('HX-SEC-001')]},headers=analyst)
    assert r.status_code==409 and '保密清除' in r.json()['detail']
    # 重复清除被拦截
    assert client.post('/purges',json={'reference':'HX-SEC-001','reason':'重复'},headers=admin).status_code==409
    # 重新分析仅用剩余试件
    run2=make_run(client,analyst,sid,'清除后复核研究')
    d2=client.get(f'/runs/{run2}',headers=admin).json()
    assert len(d2['snapshot']['records'])==199
    assert all(x['reference']!='HX-SEC-001' for x in d2['snapshot']['records'])
    finish_run(run2)
    assert client.get(f'/runs/{run2}',headers=admin).json()['state']=='completed'
    assert client.get(f'/runs/{run2}/export',headers=admin).status_code==200

def test_permissions(client):
    analyst=token(client,'analyst');viewer=token(client,'viewer')
    assert client.post('/purges',json={'reference':'HX-PERM-001','reason':'x'},headers=analyst).status_code==403
    assert client.post('/purges',json={'reference':'HX-PERM-001','reason':'x'},headers=viewer).status_code==403
    assert client.get('/purges').status_code==401
    assert client.get('/purges',headers=viewer).status_code==200

def test_worker_completion_during_purge_is_discarded(client,monkeypatch):
    admin=token(client,'admin');analyst=token(client,'analyst')
    sid=make_source(client,analyst,'竞态来源')
    refs=[rec(f'HX-B-{i:03}',moisture=28+i%20,minutes=70.0+i) for i in range(59)]
    refs.append(rec('HX-RACE-001',moisture=66.6,minutes=111.1))
    fill_records(client,analyst,sid,refs)
    run_id=make_run(client,analyst,sid,'竞态研究')
    # 计算中途发起清除：worker 完成时不得把该试件写回
    import src.worker as worker
    from src.config.database import Session
    from src.services.purge import create_purge
    from src.schemas.purge import PurgeInput
    original=worker.calculate
    def purge_mid_compute(records,parameters):
        class U:email=EMAILS['admin']
        with Session() as db:
            create_purge(db,U(),PurgeInput(reference='HX-RACE-001',reason='计算中途清除'))
        return original(records,parameters)
    monkeypatch.setattr(worker,'calculate',purge_mid_compute)
    worker.execute(run_id)
    d=client.get(f'/runs/{run_id}',headers=admin).json()
    assert d['state']=='invalidated' and d['result'] is None
    from src.services.purge import artifact_path
    assert not artifact_path(run_id).exists()
    audits=client.get('/audit',headers=admin).json()
    assert any(a['action']=='run.discarded' and a['target']==str(run_id) for a in audits)
    purge=[p for p in client.get('/purges',headers=admin).json() if p['reference']=='HX-RACE-001'][0]
    assert purge['state']=='completed'

def test_file_failure_is_resumable_and_retry_completes(client,monkeypatch):
    admin=token(client,'admin');analyst=token(client,'analyst')
    sid=make_source(client,analyst,'断点来源')
    refs=[rec(f'HX-C-{i:03}',moisture=26+i%20,minutes=60.0+i) for i in range(59)]
    refs.append(rec('HX-FILE-001',moisture=55.5,minutes=99.9))
    fill_records(client,analyst,sid,refs)
    run_id=make_run(client,analyst,sid,'断点研究')
    finish_run(run_id)
    from src.services.purge import artifact_path
    path=artifact_path(run_id)
    assert path.exists()
    # 删除结果文件持续失败（模拟清除中断）
    from pathlib import Path
    real_unlink=Path.unlink
    def flaky(self,*a,**k):
        if self==path:raise OSError('模拟文件清除中断')
        return real_unlink(self,*a,**k)
    monkeypatch.setattr(Path,'unlink',flaky)
    r=client.post('/purges',json={'reference':'HX-FILE-001','reason':'文件中断演练'},headers=admin)
    assert r.status_code==200,r.text
    purge=r.json()
    assert purge['state']=='purging'
    pending=[c for c in purge['copies'] if c['state']=='pending']
    assert len(pending)==1 and pending[0]['location']=='artifact' and '中断' in pending[0]['reason']
    assert path.exists(),'中断后文件副本仍在，等待重试'
    Path.unlink=real_unlink  # 手动恢复，避免 monkeypatch.undo() 撤销 fake_queue 补丁
    # 清除期间并发建研究不得带回该试件
    run2=make_run(client,analyst,sid,'中断期间并发研究')
    d2=client.get(f'/runs/{run2}',headers=admin).json()
    assert all(x['reference']!='HX-FILE-001' for x in d2['snapshot']['records'])
    # 重试不遗漏副本，核对完成后转为已完成
    r=client.post(f"/purges/{purge['id']}/retry",headers=admin)
    assert r.status_code==200,r.text
    done=r.json()
    assert done['state']=='completed'
    assert all(c['state']=='cleared' for c in done['copies'])
    assert not path.exists()
    assert client.post(f"/purges/{purge['id']}/retry",headers=admin).status_code==409

def test_active_download_blocks_completion_until_terminated(client):
    admin=token(client,'admin');analyst=token(client,'analyst')
    sid=make_source(client,analyst,'在途来源')
    refs=[rec(f'HX-D-{i:03}',moisture=24+i%20,minutes=55.0+i) for i in range(59)]
    refs.append(rec('HX-DL-001',moisture=44.4,minutes=88.8))
    fill_records(client,analyst,sid,refs)
    run_id=make_run(client,analyst,sid,'在途研究')
    finish_run(run_id)
    from src.services import downloads
    fake=downloads.register('run',run_id)
    try:
        r=client.post('/purges',json={'reference':'HX-DL-001','reason':'在途下载演练'},headers=admin)
        purge=r.json()
        assert purge['state']=='purging'
        dl=[c for c in purge['copies'] if c['location']=='download']
        assert dl and dl[0]['state']=='pending' and '下载' in dl[0]['reason']
    finally:
        downloads.unregister(fake)
    r=client.post(f"/purges/{purge['id']}/retry",headers=admin)
    assert r.json()['state']=='completed'

def test_seed_does_not_resurrect_purged_reference(client,monkeypatch):
    admin=token(client,'admin')
    import scripts.seed_demo as seed_demo
    import src.services.runs as runs_svc
    monkeypatch.setattr(seed_demo,'queue',types.SimpleNamespace(rpush=lambda *a:None))
    seed_demo.seed()
    r=client.post('/purges',json={'reference':'HX-202611-01-0001','reason':'seed 复活演练'},headers=admin)
    assert r.json()['state']=='completed'
    seed_demo.seed()
    from src.config.database import Session
    from src.models.record import Record
    from sqlalchemy import select
    with Session() as db:
        assert db.scalar(select(Record).where(Record.reference=='HX-202611-01-0001')) is None

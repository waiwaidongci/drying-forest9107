import json,time,logging
from redis import Redis
from sqlalchemy import select,update
from .config.database import Session
from .config.settings import REDIS_URL,ARTIFACT_DIR
from .models.run import Run
from .models import source,record,user,note,audit,purge
from .services.algorithm import calculate
from .services.audit import log
from .services.export import csv_response
queue=Redis.from_url(REDIS_URL)
ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
def execute(id):
    with Session() as db:
        r=db.get(Run,id)
        if not r or r.state!='queued':return
        r.state='running';db.commit()
        try:
            result=calculate(r.snapshot['records'],r.parameters)
            json.dumps(result,allow_nan=False)
        except Exception as e:
            logging.exception('analysis failed')
            db.refresh(r)
            if r.state=='running':
                r.state='failed';r.error=str(e)[:2000]
                log(db,'worker','run.failed',r.id,r.error);db.commit()
            return
        res=db.execute(update(Run).where(Run.id==id,Run.state=='running').values(result=result,state='completed'))
        if res.rowcount!=1:
            db.rollback()
            log(db,'worker','run.discarded',id,'任务状态已变化（可能因保密清除失效），计算结果已丢弃')
            db.commit()
            return
        log(db,'worker','run.completed',id)
        db.commit()
        try:
            response=csv_response(result,'run.csv')
            (ARTIFACT_DIR/f'run-{id}.csv').write_bytes(response.body)
        except Exception:logging.exception('artifact write failed')
def main():
    while True:
        (ARTIFACT_DIR/'heartbeat').write_text(str(time.time()))
        value=queue.blpop('analysis:queue',timeout=2)
        if value:execute(int(value[1]))
if __name__=='__main__':main()

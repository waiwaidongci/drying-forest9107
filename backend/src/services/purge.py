import time
from fastapi import HTTPException
from sqlalchemy import select
from ..config.settings import ARTIFACT_DIR
from ..models.purge import Purge
from ..models.record import Record
from ..models.run import Run
from ..models.note import Note
from ..utils.serialization import row,now
from . import downloads
from .audit import log
def purged_references(db):
    return {r for (r,) in db.execute(select(Purge.reference))}
def list_purges(db):
    return [row(p) for p in db.scalars(select(Purge).order_by(Purge.id.desc()))]
def get_purge(db,id):
    p=db.get(Purge,id)
    if not p:raise HTTPException(404,'未找到清除任务')
    return p
def artifact_path(run_id):return ARTIFACT_DIR/f'run-{run_id}.csv'
def _copy(key,location,target,reason=''):
    return {'key':key,'location':location,'target':target,'state':'pending','reason':reason}
def _scan(db,reference,targets):
    run_ids=set(targets.get('run_ids',[]));note_ids=set(targets.get('note_ids',[]));copies=[]
    for rec in db.scalars(select(Record).where(Record.reference==reference)):
        copies.append(_copy(f'record:{rec.id}','source',f'来源 #{rec.source_id}','来源样本待删除'))
    for run in db.scalars(select(Run)):
        snap_refs={r.get('reference') for r in (run.snapshot or {}).get('records') or []}
        res_refs={r.get('reference') for r in (run.result or {}).get('rows') or []}
        if reference in snap_refs:
            run_ids.add(run.id)
            copies.append(_copy(f'run-snapshot:{run.id}','snapshot',f'研究 #{run.id} · {run.name}','冻结输入包含该试件'))
            if run.result:copies.append(_copy(f'run-result:{run.id}','result',f'研究 #{run.id} · {run.name}','结论由含该试件的输入计算得出'))
        elif reference in res_refs:
            run_ids.add(run.id)
            copies.append(_copy(f'run-result:{run.id}','result',f'研究 #{run.id} · {run.name}','结果明细包含该试件'))
    for note in db.scalars(select(Note)):
        rows=(note.frozen_result or {}).get('rows') or []
        if note.run_id in run_ids or any(r.get('reference')==reference for r in rows):
            note_ids.add(note.id)
            copies.append(_copy(f'note:{note.id}','note',f'工艺备忘 #{note.id} · {note.title}','冻结结果包含该试件结论'))
    for rid in sorted(run_ids):
        copies.append(_copy(f'artifact:{rid}','artifact',f'结果文件 run-{rid}.csv','待删除'))
    return copies,{'run_ids':sorted(run_ids),'note_ids':sorted(note_ids)}
def _apply(db,reference,targets):
    for rec in db.scalars(select(Record).where(Record.reference==reference)):db.delete(rec)
    notice='因保密试件 '+reference+' 清除，本研究结论已失效；原始结果与该试件的冻结输入副本已移除，可基于剩余样本重新分析。'
    for rid in targets['run_ids']:
        run=db.get(Run,rid)
        if not run:continue
        snap=run.snapshot or {}
        run.snapshot={**snap,'records':[r for r in snap.get('records') or [] if r.get('reference')!=reference]}
        run.result=None;run.state='invalidated';run.error=notice
    for nid in targets['note_ids']:
        note=db.get(Note,nid)
        if not note:continue
        note.state='invalidated'
        note.frozen_result={'notice':'因保密试件 '+reference+' 清除，本说明冻结的结果已移除，结论失效。','reference':reference}
    db.commit()
def _verify(db,reference,targets):
    pending=[]
    for rec in db.scalars(select(Record).where(Record.reference==reference)):
        pending.append(_copy(f'record:{rec.id}','source',f'来源 #{rec.source_id}','数据库副本仍存在'))
    for run in db.scalars(select(Run)):
        snap_refs={r.get('reference') for r in (run.snapshot or {}).get('records') or []}
        res_refs={r.get('reference') for r in (run.result or {}).get('rows') or []}
        if reference in snap_refs:pending.append(_copy(f'run-snapshot:{run.id}','snapshot',f'研究 #{run.id} · {run.name}','冻结输入仍包含该试件'))
        if reference in res_refs or (reference in snap_refs and run.result):pending.append(_copy(f'run-result:{run.id}','result',f'研究 #{run.id} · {run.name}','结果仍包含该试件结论'))
    for note in db.scalars(select(Note)):
        rows=(note.frozen_result or {}).get('rows') or []
        if any(r.get('reference')==reference for r in rows) or (note.run_id in targets['run_ids'] and note.state!='invalidated'):
            pending.append(_copy(f'note:{note.id}','note',f'工艺备忘 #{note.id} · {note.title}','冻结结果尚未失效'))
    for rid in targets['run_ids']:
        if artifact_path(rid).exists():pending.append(_copy(f'artifact:{rid}','artifact',f'结果文件 run-{rid}.csv','结果文件尚未删除'))
    pairs=[('run',i) for i in targets['run_ids']]+[('note',i) for i in targets['note_ids']]
    for kind,i in downloads.active_for(pairs):
        pending.append(_copy(f'download:{kind}:{i}','download',('研究 #' if kind=='run' else '工艺备忘 #')+str(i),'已发起的下载尚未终止'))
    return pending
def execute_purge(db,purge,actor):
    reference=purge.reference
    targets=dict(purge.targets or {'run_ids':[],'note_ids':[]})
    manifest={};pending=[]
    for _ in range(6):
        copies,targets=_scan(db,reference,targets)
        _apply(db,reference,targets)
        for c in copies:
            c['state']='cleared';c['reason']='';manifest[c['key']]=c
        for kind,ids in (('run',targets['run_ids']),('note',targets['note_ids'])):
            for i in ids:downloads.cancel(kind,i)
        for rid in targets['run_ids']:
            key=f'artifact:{rid}'
            try:
                artifact_path(rid).unlink(missing_ok=True)
                manifest[key]={'key':key,'location':'artifact','target':f'结果文件 run-{rid}.csv','state':'cleared','reason':''}
            except Exception as e:
                manifest[key]={'key':key,'location':'artifact','target':f'结果文件 run-{rid}.csv','state':'pending','reason':'文件删除失败：'+str(e)[:200]}
        pending=_verify(db,reference,targets)
        for c in pending:
            old=manifest.get(c['key'])
            if old and old['state']=='pending' and old['reason']:c['reason']=old['reason']
            manifest[c['key']]=c
        if not pending:break
        if any(c['location']=='download' for c in pending):time.sleep(0.05)
    pending_keys={c['key'] for c in pending}
    for key,c in manifest.items():
        if c['state']=='pending' and key not in pending_keys:c['state']='cleared';c['reason']=''
    purge.targets=targets
    purge.copies=sorted(manifest.values(),key=lambda c:c['key'])
    purge.updated_at=now()
    if pending:
        purge.state='purging'
    else:
        purge.state='completed';purge.completed_at=now()
        log(db,actor,'purge.completed',purge.id,reference)
    db.commit()
    return purge
def create_purge(db,user,data):
    reference=data.reference.strip()
    if not reference:raise HTTPException(422,'试件编号不能为空')
    existing=db.scalar(select(Purge).where(Purge.reference==reference))
    if existing:raise HTTPException(409,'该试件编号已存在清除任务（'+('已完成' if existing.state=='completed' else '清除中')+'），请在清除页核对')
    purge=Purge(reference=reference,state='purging',reason=data.reason.strip(),copies=[],targets={'run_ids':[],'note_ids':[]},created_by=user.email,created_at=now(),updated_at=now(),completed_at='')
    db.add(purge);db.flush()
    log(db,user,'purge.created',purge.id,reference)
    db.commit()
    execute_purge(db,purge,user)
    return row(purge)
def retry_purge(db,user,id):
    purge=get_purge(db,id)
    if purge.state=='completed':raise HTTPException(409,'清除已完成，无需重试')
    log(db,user,'purge.retry',purge.id,purge.reference)
    db.commit()
    execute_purge(db,purge,user)
    return row(purge)

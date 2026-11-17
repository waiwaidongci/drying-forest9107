import csv,io,time
from fastapi import HTTPException
from fastapi.responses import Response,StreamingResponse
from ..models.note import Note
from .runs import get_run
from . import downloads
CHUNK_ROWS=25
CHUNK_PAUSE=0.015
def csv_text(result):
    buf=io.StringIO();fields=result['columns'];w=csv.DictWriter(buf,fieldnames=fields);w.writeheader()
    for r in result['rows']:w.writerow({k:r.get(k,'') for k in fields})
    return '﻿'+buf.getvalue()
def csv_response(result,filename):
    return Response(csv_text(result),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
def _stream(text,filename,kind,target_id):
    lines=text.splitlines(keepends=True)
    chunks=[''.join(lines[i:i+CHUNK_ROWS]) for i in range(0,len(lines),CHUNK_ROWS)]
    def gen():
        token=downloads.register(kind,target_id)
        try:
            for chunk in chunks:
                if downloads.is_cancelled(kind,target_id):raise downloads.DownloadCancelled('下载因保密清除被终止')
                yield chunk
                time.sleep(CHUNK_PAUSE)
        finally:downloads.unregister(token)
    return StreamingResponse(gen(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
def run_csv(db,id):
    r=get_run(db,id)
    if r.state=='invalidated':raise HTTPException(409,'该研究结论已因保密试件清除失效，禁止查看与下载')
    if r.state!='completed':raise HTTPException(409,'结果尚未完成')
    if downloads.is_cancelled('run',id):raise HTTPException(409,'该结果涉及保密清除，下载已终止')
    return _stream(csv_text(r.result),'run-'+str(id)+'.csv','run',id)
def note_csv(db,id):
    n=db.get(Note,id)
    if not n:raise HTTPException(404,'未找到说明')
    if n.state=='invalidated':raise HTTPException(403,'该业务说明已因保密试件清除失效，禁止下载')
    if n.state!='approved':raise HTTPException(403,'仅批准后的业务说明可导出')
    if downloads.is_cancelled('note',id):raise HTTPException(403,'该说明涉及保密清除，下载已终止')
    return _stream(csv_text(n.frozen_result),'approved-'+str(id)+'.csv','note',id)

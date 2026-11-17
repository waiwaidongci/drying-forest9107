import threading,uuid
_lock=threading.Lock()
_active={}
_cancel=set()
class DownloadCancelled(Exception):pass
def register(kind,target_id):
    token=uuid.uuid4().hex
    with _lock:_active[token]=(kind,int(target_id))
    return token
def unregister(token):
    with _lock:_active.pop(token,None)
def cancel(kind,target_id):
    with _lock:_cancel.add((kind,int(target_id)))
def is_cancelled(kind,target_id):
    with _lock:return (kind,int(target_id)) in _cancel
def active_for(pairs):
    pairs={(k,int(i)) for k,i in pairs}
    with _lock:return sorted({v for v in _active.values() if v in pairs})
def reset():
    with _lock:_active.clear();_cancel.clear()

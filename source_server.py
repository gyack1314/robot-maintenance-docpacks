"""本地演练资料服务，提供页面、原件与收件回执。"""
from __future__ import annotations
import argparse, copy, hashlib, json, mimetypes, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import provider
ROOT=Path(__file__).resolve().parent
CONFIG=json.loads((ROOT/'sources'/'source.json').read_text(encoding='utf-8'))
LOCK=threading.RLock()
STATE=copy.deepcopy(CONFIG['initial_state'])
RECEIPTS=[]
JOBS=[]
TEST_BASE=''
def encoded(value): return json.dumps(value,ensure_ascii=False).encode('utf-8')
def reset():
    global STATE
    with LOCK:
        STATE=copy.deepcopy(CONFIG['initial_state']);RECEIPTS.clear();JOBS.clear()
def get_rows(name):
    return RECEIPTS if name=='receipts' else (provider.read(STATE) if name=='provider_records' else STATE['collections'].get(name,[]))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,body,status=200,kind='application/json; charset=utf-8',headers=None):
        data=body if isinstance(body,bytes) else body.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        u=urlparse(self.path);q=parse_qs(u.query)
        if u.path=='/health': return self.send(encoded({'ready':True,'source':CONFIG['name']}))
        if u.path=='/': return self.send((ROOT/'sources'/'portal.html').read_bytes(),kind='text/html; charset=utf-8')
        if u.path=='/source-meta':
            return self.send(encoded({'name':CONFIG['name'],'revision':STATE['revision'],'provider':{'kind':STATE['provider']['kind'],'version':STATE['provider']['version']},
                'collections':[{'name':name,'total':len(get_rows(name))} for name,rows in STATE['collections'].items()]+[{'name':'receipts','total':len(RECEIPTS)}],
                'operations':CONFIG['operations'],'changes':[{'id':x['id'],'label':x['label']} for x in CONFIG.get('changes',[])]}))
        if u.path=='/export-status':
            job=next((x for x in JOBS if x['id']==q.get('id',[''])[0]),None)
            if job is None:return self.send(encoded({'error':'job_not_found'}),404)
            job['checks']+=1
            status='generating' if job['checks']==1 else 'ready'
            return self.send(encoded({'id':job['id'],'revision':job['revision'],'state':('expired' if time.time()>job['expires'] else status),
                'manifest':job['manifest'] if status=='ready' and time.time()<=job['expires'] else []}))
        if u.path=='/source-state':
            return self.send(encoded({'error':'not_found'}),404)
        if u.path=='/source-detail':
            name=q.get('name',[''])[0]; rid=q.get('id',[''])[0]
            row=next((x for x in get_rows(name) if x['id']==rid),None)
            return self.send(encoded({'revision':STATE['revision'],'item':row}),200 if row else 404)
        if u.path=='/source-list':
            name=q.get('name',[''])[0]
            with LOCK:
                rows=copy.deepcopy(get_rows(name)); revision=STATE['revision']
            start=int(q.get('offset',['0'])[0]);size=2
            page=rows[start:start+size]
            if name in CONFIG.get('browser_collections',[]):page=[{k:v for k,v in x.items() if k in ('id','name','label')} for x in page]
            return self.send(encoded({'revision':revision,'name':name,'items':page,
                'next':f'/source-list?name={name}&offset={start+size}' if start+size<len(rows) else None,'total':len(rows)}))
        if u.path=='/receipt':
            key=q.get('request',[''])[0]
            with LOCK: matches=[x for x in RECEIPTS if x['request_key']==key]
            return self.send(encoded({'items':matches}))
        if u.path=='/events':
            after=q.get('after',[''])[0]
            if after=='expired': return self.send(encoded({'error':'cursor_expired','archive':'/files/archive.json'}),410)
            try:position=int(after or '0')
            except ValueError:return self.send(encoded({'error':'invalid_cursor'}),400)
            events=[x for x in STATE['collections'].get('events',[]) if x['sequence']>position]
            data=''.join('id: '+str(x['sequence'])+'\nevent: record\ndata: '+json.dumps(x,ensure_ascii=False)+'\n\n' for x in events)
            return self.send(data,kind='text/event-stream; charset=utf-8')
        if u.path.startswith('/files/'):
            name=u.path.removeprefix('/files/')
            path=(ROOT/'sources'/'files'/name).resolve()
            base=(ROOT/'sources'/'files').resolve()
            if not path.is_relative_to(base) or not path.is_file():return self.send(encoded({'error':'not_found'}),404)
            data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
            r=self.headers.get('Range')
            headers={'ETag':'"'+digest+'"','X-Content-SHA256':digest,'Accept-Ranges':'bytes'}
            if r:
                start,end=r.removeprefix('bytes=').split('-',1);a=int(start);b=int(end) if end else len(data)-1
                if a>=len(data):return self.send(b'',416,headers={'Content-Range':f'bytes */{len(data)}'})
                b=min(b,len(data)-1);headers['Content-Range']=f'bytes {a}-{b}/{len(data)}'
                return self.send(data[a:b+1],206,kind=mimetypes.guess_type(name)[0] or 'application/octet-stream',headers=headers)
            return self.send(data,kind=mimetypes.guess_type(name)[0] or 'application/octet-stream',headers=headers)
        return self.send(encoded({'error':'not_found'}),404)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'));value=json.loads(self.rfile.read(length) or b'{}')
        except (ValueError,TypeError):return self.send(encoded({'error':'invalid_json'}),400)
        if self.path=='/export-start':
            if value.get('expected_revision')!=STATE['revision']:
                return self.send(encoded({'error':'revision_conflict'}),409)
            job={'id':'J'+str(len(JOBS)+1),'revision':STATE['revision'],'checks':0,'expires':time.time()+CONFIG.get('export_ttl',120),'manifest':copy.deepcopy(CONFIG.get('export_manifest',[]))}
            JOBS.append(job);return self.send(encoded({'id':job['id'],'state':'submitted'}),202)
        if self.path=='/exercise':
            selected=next((x for x in CONFIG.get('changes',[]) if x['id']==value.get('id')),None)
            if not selected:return self.send(encoded({'error':'unknown_change'}),404)
            with LOCK:
                for patch in selected.get('patches',[]):
                    rows=STATE['collections'][patch['collection']]
                    target=next((x for x in rows if x['id']==patch['id']),None)
                    if target is not None:target.update(patch['set'])
                    elif 'append' in patch:rows.append(patch['append'])
                p=STATE['provider']
                for changed in selected.get('provider_patches',[]):
                    next(x for x in p['records'] if x['id']==changed['id']).update(copy.deepcopy(changed['set']))
                p['accounts'].update(copy.deepcopy(selected.get('provider_accounts',{})))
                if selected.get('provider_patches') or selected.get('provider_accounts'):p['version']+=1
                STATE.setdefault('action_outcomes',{}).update(copy.deepcopy(selected.get('action_outcomes',{})))
                if 'export_manifest' in selected:CONFIG['export_manifest']=copy.deepcopy(selected['export_manifest'])
                STATE['revision']+=1
            return self.send(encoded({'changed':selected['id'],'revision':STATE['revision']}))
        if self.path!='/source-action':return self.send(encoded({'error':'not_found'}),404)
        with LOCK:
            existing=next((x for x in RECEIPTS if x['request_key']==value.get('request_key')),None)
            if existing:
                if any(existing[k]!=value.get(k) for k in ('operation','object_id')) or existing['input']!=value.get('payload',{}):
                    return self.send(encoded({'error':'request_key_reused'}),409)
                return self.send(encoded(existing))
            if not value.get('request_key') or not value.get('object_id') or value.get('operation') not in CONFIG['operations']:
                return self.send(encoded({'error':'invalid_action'}),400)
            candidate=copy.deepcopy(STATE)
            try:result=provider.invoke(CONFIG,candidate,value)
            except provider.Rejected as exc:return self.send(encoded({'error':exc.reason}),exc.status)
            except (ValueError,TypeError,KeyError,StopIteration):return self.send(encoded({'error':'invalid_provider_payload'}),422)
            STATE.clear();STATE.update(candidate)
            rid='R'+str(len(RECEIPTS)+1).zfill(4)
            receipt={'id':rid,'receipt_id':rid,'request_key':value['request_key'],'object_id':value['object_id'],
                'operation':value['operation'],'input':copy.deepcopy(value.get('payload',{})),**result}
            RECEIPTS.append(receipt);STATE['revision']+=1
            if value.get('drop_response'):
                self.close_connection=True
                try:self.connection.shutdown(2)
                except OSError:pass
                self.connection.close();return
            return self.send(encoded(receipt),201)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'演练资料服务：http://127.0.0.1:{server.server_address[1]}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()


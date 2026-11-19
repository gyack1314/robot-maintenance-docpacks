"""核验来源系统，不包含业务采集器。"""
import copy, hashlib, json, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
import source_server as source
class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),source.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_address[1])
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):source.reset()
    def call(self,path,value=None,headers=None):
        data=None if value is None else json.dumps(value).encode()
        req=urllib.request.Request(self.base+path,data=data,headers=headers or {})
        with urllib.request.urlopen(req,timeout=4) as r:return r.status,r.read(),dict(r.headers)
    def test_health_and_paging(self):
        self.assertTrue(json.loads(self.call('/health')[1])['ready'])
        for name,expected in source.STATE['collections'].items():
            actual=[];path='/source-list?name='+name
            while path:
                page=json.loads(self.call(path)[1]);actual.extend(page['items']);path=page['next']
            self.assertEqual([x['id'] for x in actual],[x['id'] for x in expected])
            if expected:
                detail=json.loads(self.call('/source-detail?name='+name+'&id='+expected[-1]['id'])[1])
                self.assertEqual(detail['item'],expected[-1])
    def test_action_validation_conflict_and_deduplication(self):
        key,expected=next(iter(source.STATE['action_outcomes'].items()));operation,obj=key.split(':',1)
        payload={field:'source-test' for field in source.CONFIG['required_fields'].get(operation,[])}
        payload.update(expected.get('equals',{}))
        for field,allowed in expected.get('allowed_items',{}).items():payload[field]=allowed[:1]
        value={'operation':operation,'object_id':obj,'request_key':'source-test-1','expected_revision':0,'payload':payload}
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/source-action',value)
        self.assertEqual(error.exception.code,409)
        value['expected_revision']=source.STATE['revision']
        invalid=copy.deepcopy(value);invalid['payload']={}
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/source-action',invalid)
        self.assertEqual(error.exception.code,422)
        first=json.loads(self.call('/source-action',value)[1]);second=json.loads(self.call('/source-action',value)[1])
        self.assertEqual(first,second);self.assertEqual(len(source.RECEIPTS),1)
        self.assertEqual(first['accepted'],expected['accepted'])
        reused=copy.deepcopy(value);reused['payload']['changed']=True
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/source-action',reused)
        self.assertEqual(error.exception.code,409)
    def test_committed_response_loss_is_queryable(self):
        key,expected=next(iter(source.STATE['action_outcomes'].items()));operation,obj=key.split(':',1)
        payload={field:'source-test' for field in source.CONFIG['required_fields'].get(operation,[])};payload.update(expected.get('equals',{}))
        for field,allowed in expected.get('allowed_items',{}).items():payload[field]=allowed[:1]
        value={'operation':operation,'object_id':obj,'request_key':'lost-1','expected_revision':1,'payload':payload,'drop_response':True}
        try:self.call('/source-action',value)
        except Exception:pass
        receipt=json.loads(self.call('/receipt?request=lost-1')[1])['items']
        self.assertEqual(len(receipt),1);self.assertEqual(receipt[0]['input'],payload)
    def test_changes_preserve_independent_source_facts(self):
        source.reset()
        for change in source.CONFIG['changes']:
            old=source.STATE['revision']
            self.call('/exercise',{'id':change['id']});self.assertEqual(source.STATE['revision'],old+1)
            for p in change.get('patches',[]):
                target=next(x for x in source.STATE['collections'][p['collection']] if x['id']==p['id'])
                for k,v in p.get('set',{}).items():self.assertEqual(target[k],v)
    def test_real_assets_and_ranges(self):
        base=Path('sources/files')
        for path in base.rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to(base).as_posix();data=path.read_bytes()
            status,body,headers=self.call('/files/'+relative)
            self.assertEqual(body,data);self.assertEqual(headers['X-Content-SHA256'],hashlib.sha256(data).hexdigest())
            if data:
                status,body,headers=self.call('/files/'+relative,headers={'Range':'bytes=0-7'})
                self.assertEqual(status,206);self.assertEqual(body,data[:8])
    def test_export_snapshot_is_frozen(self):
        if not source.CONFIG.get('export_manifest'):self.skipTest('该来源不提供异步导出')
        manifest=copy.deepcopy(source.CONFIG['export_manifest'])
        job=json.loads(self.call('/export-start',{'expected_revision':source.STATE['revision']})[1])
        self.assertEqual(json.loads(self.call('/export-status?id='+job['id'])[1])['state'],'generating')
        result=json.loads(self.call('/export-status?id='+job['id'])[1]);self.assertEqual(result['manifest'],manifest)
    def test_sse_resume_and_archive(self):
        if 'events' not in source.STATE['collections']:self.skipTest('该来源不提供事件流')
        data=self.call('/events?after=11')[1].decode();self.assertIn('event: record\n',data)
        self.assertNotIn('id: 11\n',data);self.assertIn('id: 12\n',data)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/events?after=expired')
        self.assertEqual(error.exception.code,410)
if __name__=='__main__':unittest.main()


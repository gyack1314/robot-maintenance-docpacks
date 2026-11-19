"""核验来源系统及独立收件端，不包含业务采集器。"""
import hashlib,json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
import source_server as source
class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),source.Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_address[1]);source.TEST_BASE=cls.base
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):source.reset()
    def call(self,path,value=None,headers=None):
        data=None if value is None else json.dumps(value).encode()
        request=urllib.request.Request(self.base+path,data=data,headers=headers or {})
        with urllib.request.urlopen(request,timeout=4) as response:return response.status,response.read(),dict(response.headers)
    def test_health_and_paging(self):
        self.assertTrue(json.loads(self.call('/health')[1])['ready'])
        for name in list(source.STATE['collections'])+['provider_records']:
            expected=source.get_rows(name);actual=[];path='/source-list?name='+name
            while path:
                page=json.loads(self.call(path)[1]);actual.extend(page['items']);path=page['next']
            self.assertEqual([x['id'] for x in actual],[x['id'] for x in expected],name)
            if expected:
                detail=json.loads(self.call('/source-detail?name='+name+'&id='+expected[-1]['id'])[1]);self.assertEqual(detail['item'],expected[-1])
    def test_provider_rejects_stale_version_without_mutation(self):
        before=json.loads(json.dumps(source.STATE['provider']));operation=source.CONFIG['operations'][0]
        value={'operation':operation,'object_id':'missing','request_key':'stale','payload':{key:('stale' if key!='provider_version' else 0) for key in source.CONFIG['required_fields'][operation]}}
        request=urllib.request.Request(self.base+'/source-action',data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=4)
        self.assertEqual(error.exception.code,409);self.assertEqual(source.STATE['provider'],before);self.assertFalse(source.RECEIPTS)
    def test_changes_preserve_independent_source_facts(self):
        source.reset()
        for change in source.CONFIG['changes']:
            old=source.STATE['revision'];self.call('/exercise',{'id':change['id']});self.assertEqual(source.STATE['revision'],old+1)
            for patch in change.get('patches',[]):
                if 'append' in patch:self.assertIn(patch['append'],source.STATE['collections'][patch['collection']])
                else:
                    target=next(x for x in source.STATE['collections'][patch['collection']] if x['id']==patch['id'])
                    for key,value in patch.get('set',{}).items():self.assertEqual(target[key],value)
            for patch in change.get('provider_patches',[]):
                target=next(x for x in source.STATE['provider']['records'] if x['id']==patch['id'])
                for key,value in patch['set'].items():self.assertEqual(target[key],value)
    def test_real_assets_and_ranges(self):
        for path in Path('sources/files').rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to('sources/files').as_posix();data=path.read_bytes()
            _,body,headers=self.call('/files/'+relative);self.assertEqual(body,data);self.assertEqual(headers['X-Content-SHA256'],hashlib.sha256(data).hexdigest())
            if data:
                status,body,_=self.call('/files/'+relative,headers={'Range':'bytes=0-7'});self.assertEqual(status,206);self.assertEqual(body,data[:8])
    def test_export_snapshot_is_frozen(self):
        if not source.CONFIG.get('export_manifest'):self.skipTest('该来源不提供异步导出')
        manifest=source.CONFIG['export_manifest'];job=json.loads(self.call('/export-start',{'expected_revision':source.STATE['revision']})[1])
        self.assertEqual(json.loads(self.call('/export-status?id='+job['id'])[1])['state'],'generating')
        self.assertEqual(json.loads(self.call('/export-status?id='+job['id'])[1])['manifest'],manifest)
    def test_sse_resume_and_archive(self):
        if 'events' not in source.STATE['collections']:self.skipTest('该来源不提供事件流')
        data=self.call('/events?after=11')[1].decode();self.assertIn('event: record\n',data)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/events?after=expired')
        self.assertEqual(error.exception.code,410)
from provider_scenarios import ProviderScenarios
if __name__=='__main__':unittest.main()


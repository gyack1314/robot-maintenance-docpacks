import copy,hashlib,json,unittest,urllib.request,urllib.error
import source_server as source
def payload(op,**fields):return {'operation':op,'object_id':fields.pop('object_id'),'request_key':'case-'+str(source.STATE['provider']['serial']+1),'payload':{'provider_version':source.STATE['provider']['version'],**fields}}
class ProviderScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        cls.server=source.ThreadingHTTPServer(('127.0.0.1',0),source.Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        source.TEST_BASE='http://127.0.0.1:'+str(cls.server.server_address[1])
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def send(self,request,drop=False):
        request=copy.deepcopy(request);request['drop_response']=drop
        req=urllib.request.Request(source.TEST_BASE+'/source-action',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=3) as r:return json.loads(r.read())
        except urllib.error.HTTPError as e:raise AssertionError((e.code,e.read().decode()))
        except OSError:
            if not drop:raise
            return {'request_key':request['request_key']}
    def reject(self,request,status):
        req=urllib.request.Request(source.TEST_BASE+'/source-action',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req,timeout=3)
        self.assertEqual(e.exception.code,status,e.exception.read().decode())
    def exercise(self,id):
        req=urllib.request.Request(source.TEST_BASE+'/exercise',data=json.dumps({'id':id}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=3):pass
    def test_stateful_business_receipt_and_unknown_response(self):
        kind=source.STATE['provider']['kind'];source.reset()
        if kind=='laundry':
            r=self.send(payload('reserve_return',object_id='BG-01',stay='ST-A',destination='前台A',items=['IT-1','IT-2'],mandate_revision=1,shift='night-1'),True)
            receipt=json.loads(urllib.request.urlopen(source.TEST_BASE+'/receipt?request='+r['request_key'],timeout=3).read())['items'][0]
            reservation=receipt['accepted'][0]['reservation'];self.assertEqual(source.STATE['provider']['records'][0]['state'],'reserved')
            self.send(payload('dispatch_return',object_id='BG-01',reservation=reservation,shift='night-1'))
            self.assertEqual(source.STATE['provider']['records'][0]['state'],'in_transit');self.exercise('checkout')
            self.reject(payload('dispatch_return',object_id='BG-02',reservation='stale',shift='night-1'),409)
        elif kind=='replenishment':
            r=self.send(payload('place_order',object_id='C1',packs=3,store='S1',round='R18',basis=['P1','W1']))
            self.assertEqual(r['status'],'partial');order=r['accepted'][0]['order']
            self.send(payload('hold_stock',object_id='W1',pieces=4,round='R18'))
            self.send(payload('receive_order',object_id=order,pieces=3,stock='W1'))
            self.assertEqual(source.STATE['provider']['records'][0]['available'],3)
        elif kind=='recall':
            self.send(payload('attach_hold',object_id='S1-A18',inventory_version=11,store='S1',quantity=7,reason='VA:N18:2'))
            self.reject(payload('detach_hold',object_id='S1-A18',inventory_version=12,reason='MANUAL-RISK'),409)
            self.send(payload('detach_hold',object_id='S1-A18',inventory_version=12,reason='VA:N18:2'))
            self.assertEqual(source.STATE['provider']['records'][0]['holds'],{'MANUAL-RISK':1,'VA:N18:1':2})
        elif kind=='episode':
            r=self.send(payload('stage_package',object_id='AS1',permit='AUTH1',permit_revision=1,recipient='MAINT-A',purpose='maintenance',manifest=[],event_boundary='E11-E14'))
            self.exercise('restrict');self.reject(payload('claim_package',object_id=r['accepted'][0]),409)
        elif kind=='docdesk':
            data=b'manual';digest=hashlib.sha256(data).hexdigest()
            self.send(payload('register_blob',object_id=digest,sha256=digest,hex=data.hex(),vendor='V1'))
            self.reject(payload('stage_bundle',object_id='RB1',device='RB1',observed_at='now',documents=['MAIN'],manifest=[{'sha256':'missing'}],seat='NIGHT-A'),409)
            r=self.send(payload('stage_bundle',object_id='RB1',device='RB1',observed_at='now',documents=['MAIN'],manifest=[{'sha256':digest}],seat='NIGHT-A'))
            self.send(payload('receive_bundle',object_id=r['accepted'][0],seat='NIGHT-A',seat_generation=1))
            self.assertEqual(source.STATE['provider']['records'][0]['generation'],2)
        elif kind=='access':
            self.send(payload('revoke_permit',object_id='CR0'));self.assertEqual(source.STATE['provider']['records'][0]['state'],'exit_confirmation')
            self.reject(payload('confirm_exit',object_id='CR0'),409);self.exercise('scan_exit');self.send(payload('confirm_exit',object_id='CR0'))
            self.assertEqual(source.STATE['provider']['records'][0]['state'],'revoked')
        elif kind=='finance':
            data=b'cover';digest=hashlib.sha256(data).hexdigest();self.send(payload('register_original',object_id=digest,hex=data.hex(),vendor='VA'))
            r=self.send(payload('intake_lines',object_id=digest,originals=[digest],lines=[{'identity':'A1-copy','charge':'CH1','amount':'100.00','currency':'CNY'},{'identity':'A2','charge':'CH2','amount':'30.00','currency':'CNY'},{'identity':'CR-too-large','charge':'CH1','amount':'-200.00','currency':'CNY','credit_for':'VA/B100/1/A1'}]))
            self.assertEqual((r['status'],r['accepted']),('partial',['A2']))
        elif kind=='labels':
            r=self.send(payload('dispatch_labels',object_id='G1',positions=[{'position':'P1','inspection':'I1@1','basis':'TP1','desired_price':'5.50','route':'replace_label','print_group':'TEA'},{'position':'P2','inspection':'I2@1','basis':'TP2','desired_price':'5.00','route':'replace_label','print_group':'TEA'}]))
            self.assertEqual((r['status'],len(r['accepted'])),('partial',1));work=r['accepted'][0]
            self.send(payload('execute_label',object_id=work,image='/files/new.svg'));self.reject(payload('verify_label',object_id=work,inspection='I2@1'),422)
            self.send(payload('verify_label',object_id=work,inspection='I2@2'))
        elif kind=='complaints':
            r=self.send(payload('route_matter',object_id='CLAIM-2',claim='CLAIM-2',responsibility='package_damage',recipient='LAUNDRY-A',purpose='damage_verification',task='T200',sources=['M2'],attachments=[]))
            matter=r['accepted'][0];self.send(payload('withdraw_matter',object_id=matter));self.reject(payload('confirm_matter',object_id=matter,confirmation='none'),409)
            next(x for x in source.STATE['provider']['records'] if x['id']==matter)['receiver_confirmation']='conf1'
            self.send(payload('confirm_matter',object_id=matter,confirmation='conf1'))
            self.assertEqual(next(x for x in source.STATE['provider']['records'] if x['id']==matter)['state'],'withdrawn')
        elif kind=='inspection':
            data=b'photo';digest=hashlib.sha256(data).hexdigest()
            self.send(payload('upload_photo',object_id='PH1',sha256=digest,hex=data.hex(),task='TASK-30',field='F-TEMP'))
            r=self.send(payload('sync_observation',object_id='TASK-30|RB1|F-TEMP',task='TASK-30',device='RB1',field='F-TEMP',form_revision=1,meaning='clear',observed_at='2026-11-18T19:30:00+08:00',source_observation='LOCAL-CLEAR',base_receipt='OB1'),True)
            receipt=json.loads(urllib.request.urlopen(source.TEST_BASE+'/receipt?request='+r['request_key'],timeout=3).read())['items'][0]
            self.assertEqual(receipt['status'],'received')
            self.reject(payload('sync_observation',object_id='TASK-30|RB1|F-TEMP',task='TASK-30',device='RB1',field='F-TEMP',form_revision=1,meaning='missing',observed_at='later',source_observation='LOCAL-MISSING',base_receipt=receipt['accepted'][0]),422)
        self.assertEqual(len(source.RECEIPTS),source.STATE['provider']['serial'])


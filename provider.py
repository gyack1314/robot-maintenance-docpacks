"""来源系统的业务收件端；不执行采集、归并、选型或自动办理。"""
import copy, hashlib, json
from decimal import Decimal
class Rejected(Exception):
    def __init__(self,reason,status=422):self.reason=reason;self.status=status
def need(ok,reason,status=422):
    if not ok:raise Rejected(reason,status)
def find(rows,id):
    value=next((x for x in rows if x['id']==id),None);need(value is not None,'object_not_found',404);return value
def read(state):
    p=state['provider']
    return [{'id':'PROVIDER','version':p['version'],'kind':p['kind'],'accounts':p.get('accounts',{})}]+copy.deepcopy(p['records'])
def invoke(config,state,request):
    """按来源端合同原子受理一个命令，仅影响该接收方的资源。"""
    p=state['provider'];op=request['operation'];obj=request['object_id'];data=request.get('payload',{})
    need(isinstance(data,dict),'payload_must_be_object',400)
    required=config['required_fields'].get(op,[])
    need(all(k in data and data[k] is not None for k in required),'required_fields')
    need(data.get('provider_version')==p['version'],'provider_version_conflict',409)
    kind=p['kind'];records=p['records'];accounts=p.setdefault('accounts',{})
    rid='P'+str(p.get('serial',0)+1).zfill(4);accepted=[];returned=[];status='accepted'
    if True:
        if op=='stage_bundle':
            need(data['device']==obj,'device_mismatch')
            registered={r['id']:r for r in records if r.get('kind')=='blob'}
            need(all(x['sha256'] in registered for x in data['manifest']),'blob_not_uploaded',409)
            records.append({'id':rid,'kind':'bundle','device':obj,'observed_at':data['observed_at'],'manifest':data['manifest'],'documents':data['documents'],'state':'staged','seat':data['seat'],'replaces':data.get('replaces')});accepted=[rid];status='staged'
        elif op=='register_blob':
            need(data['sha256']==obj,'digest_identity_mismatch')
            body=bytes.fromhex(data['hex']);need(hashlib.sha256(body).hexdigest()==obj,'digest_mismatch')
            if not any(x['id']==obj for x in records):records.append({'id':obj,'kind':'blob','bytes':len(body)})
            accepted=[obj]
        elif op=='receive_bundle':
            bundle=find(records,obj);need(bundle['kind']=='bundle' and bundle['state']=='staged','bundle_not_ready',409)
            need(bundle['seat']==data['seat'],'wrong_seat')
            current=find(records,data['seat']);need(current['generation']==data['seat_generation'],'seat_changed',409)
            old=current.get('current_bundle');current.update(generation=current['generation']+1,current_bundle=obj);bundle.update(state='received',previous_bundle=old);accepted=[{'bundle':obj,'previous':old}];status='received'
        elif op=='withdraw_bundle':
            bundle=find(records,obj);need(bundle['state']=='staged','already_received',409);bundle['state']='withdrawn';accepted=[obj]
        elif op=='announce_update':
            bundle=find(records,obj);need(bundle['state']=='received','not_received',409);bundle.setdefault('updates',[]).append(data['observed_at']);accepted=[obj]
        else:raise Rejected('unknown_operation',400)
    p['serial']=p.get('serial',0)+1;p['version']+=1
    return {'provider_receipt':rid,'status':status,'accepted':accepted,'returned':returned,'provider_version':p['version']}


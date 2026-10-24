import json,threading,urllib.request,urllib.error,http.cookiejar,uuid
from webapp import create_app,create_http_server,close_http_server
from test_erp import ERPTests
import server as app

def main():
 fixture=ERPTests();fixture.setUp();srv=create_http_server(create_app());threading.Thread(target=srv.run,daemon=True).start();base='http://127.0.0.1:'+str(srv.effective_port);jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));csrf='';checks=0
 def req(path,data=None,key=None,token=None):
  headers={'Content-Type':'application/json','X-CSRF-Token':csrf if token is None else token,'Idempotency-Key':key or str(uuid.uuid4())};r=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
  try:
   with client.open(r,timeout=10) as response:return response.status,json.loads(response.read())
  except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
 def check(condition,label):
  nonlocal checks
  assert condition,label
  checks+=1
 try:
  code,_=req('/api/state');check(code==401,'unauthenticated state blocked')
  code,j=req('/api/login',{'username':'test-admin','password':'wrong-password'});check(code==401,'bad password rejected')
  code,j=req('/api/login',{'username':'test-admin','password':'Test-password-2026'});csrf=j['csrf'];check(code==200 and bool(jar),'cookie session created')
  code,_=req('/api/customers',{'name':'DEMO','address':'DEMO'},token='bad');check(code==403,'CSRF enforced')
  key=str(uuid.uuid4());payload={'name':'DEMO HTTP Customer','address':'DEMO address'};code,a=req('/api/customers',payload,key);code,b=req('/api/customers',payload,key);check(a==b,'duplicate save replays result')
  code,_=req('/api/customers',{**payload,'name':'DEMO changed'},key);check(code==409,'key cannot be reused for changed payload')
  code,s=req('/api/state');check(len(s['customers'])==1,'duplicate save created one record')
  fixture.validate();fixture.stock();inputs=fixture.inputs();code,calc=req('/api/calculate',inputs);check(code==200 and calc['ready'],'HTTP backend costing complete')
  pi_data={'customer_id':a['id'],'currency':'BDT','fx':'120','date':'2026-10-24','payment_terms':'DEMO sight','lines':[{'calculation_id':calc['calculation_id'],'inputs':inputs}]};code,pi=req('/api/pis',pi_data);check(code==200,'HTTP PI save')
  code,_=req('/api/pis/approve',{'id':pi['id']});check(code==200,'HTTP approval')
  lc_data={'pi_id':pi['id'],'number':'DEMO HTTP LC','lc_date':'2026-10-24','bank':'DEMO bank','amount':'100','status':'Draft'};code,lc=req('/api/lcs',lc_data);check(code==200,'HTTP LC draft')
  code,s=req('/api/state');check(s['pis'][0]['status']=='Awaiting LC','draft leaves PI in active register')
  code,_=req('/api/lcs',{**lc_data,'id':lc['id'],'status':'Confirmed'});check(code==200,'HTTP LC confirm')
  code,s=req('/api/state');check(s['pis'][0]['status']=='LC Created' and s['lcs'][0]['pi_id']==pi['id'],'PI transfer preserves link')
  code,_=req('/api/collections',{'lc_id':lc['id'],'amount':'25','date':'2026-10-24','reference':'DEMO transfer'});check(code==200,'HTTP partial collection')
  code,s=req('/api/state');check(s['lcs'][0]['outstanding']=='75','outstanding computed')
  code,_=req('/api/users',{'name':'DEMO viewer','username':'viewer','password':'Demo-viewer-2026','role':'Viewer'});check(code==200,'role created')
  code,_=req('/api/logout',{});check(code==200,'logout')
  code,j=req('/api/login',{'username':'viewer','password':'Demo-viewer-2026'});csrf=j['csrf'];code,_=req('/api/lcs',lc_data);check(code==403,'HTTP role permission blocks write')
  code,s=req('/api/state');check(not s['audit'] and not s['users'],'restricted administrative data')
  code,_=req('/api/logout',{});code,_=req('/api/state');check(code==401,'session invalidated')
  print(str(checks)+' HTTP integration checks passed. Test database is isolated and disposable.')
 finally:close_http_server(srv);fixture.tearDown()
if __name__=='__main__':main()

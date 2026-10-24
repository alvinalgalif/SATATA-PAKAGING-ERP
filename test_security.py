import io,json,hashlib,sqlite3,unittest,uuid
from dataclasses import replace
from pathlib import Path
from contextlib import closing
import server as core
from security import Settings,totp,token_hash
from webapp import create_app
from cryptography.fernet import Fernet
import test_erp as fixtures
import maintenance

class Client:
 def __init__(self,app,production=False):self.app=app;self.cookie='';self.csrf='';self.production=production
 def request(self,path,data=None,method=None,headers=None,scheme=None,peer='127.0.0.1',raw=None):
  method=method or ('POST' if data is not None or raw is not None else 'GET');body=raw if raw is not None else json.dumps(data).encode() if data is not None else b''
  env={'REQUEST_METHOD':method,'PATH_INFO':path,'HTTP_HOST':'erp.example.test' if self.production else '127.0.0.1:8765','SERVER_PORT':'8765','REMOTE_ADDR':peer,'wsgi.url_scheme':scheme or ('https' if self.production else 'http'),'wsgi.input':io.BytesIO(body),'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':'application/json','HTTP_COOKIE':self.cookie,'HTTP_X_CSRF_TOKEN':self.csrf,'HTTP_IDEMPOTENCY_KEY':str(uuid.uuid4())}
  for k,v in (headers or {}).items():env['CONTENT_TYPE' if k.lower()=='content-type' else 'HTTP_'+k.upper().replace('-','_')]=v
  result={}
  def start(status,out):result.update(status=int(status.split()[0]),headers=dict(out))
  payload=b''.join(self.app(env,start));result['raw']=payload
  try:result['body']=json.loads(payload) if payload else {}
  except (ValueError,UnicodeDecodeError):result['body']=payload
  cookie=result['headers'].get('Set-Cookie')
  if cookie:self.cookie=cookie.split(';')[0]
  if isinstance(result['body'],dict) and result['body'].get('csrf'):self.csrf=result['body']['csrf']
  return result
 def login(self,password='Test-password-2026',code=''):return self.request('/api/login',{'username':'test-admin','password':password,'code':code})

class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.ERPTests();self.fixture.setUp();self.now=1800000000;self.settings=Settings();self.app=create_app(self.settings,lambda:self.now);self.client=Client(self.app)
 def tearDown(self):self.fixture.tearDown()
 def read(self,q,args=()):return self.fixture.read(q,args)
 def production(self):
  cfg=Settings(production=True,public_origin='https://erp.example.test',setup_token='DEMO-only-bootstrap-token-'+'x'*32,mfa_key=Fernet.generate_key().decode())
  app=create_app(cfg,lambda:self.now);return app,Client(app,True)
 def enroll(self,client):
  started=client.request('/api/security/mfa/enroll',{'current_password':'Test-password-2026'});self.assertEqual(started['status'],200);secret=started['body']['secret'];code=totp(secret,int(self.now//30));confirmed=client.request('/api/security/mfa/confirm',{'code':code});self.assertEqual(confirmed['status'],200);return secret,confirmed['body']['recovery_codes']
 def test_password_policy_and_legacy_upgrade(self):
  with self.assertRaises(core.RuleError):core.hash_password('short-password')
  with self.assertRaises(core.RuleError):core.hash_password('x'*129)
  salt='demo-salt';password='OldPass123!';old=salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),250000).hex()
  with core.database(True) as c:c.execute('UPDATE users SET password=? WHERE id=1',(old,))
  self.assertEqual(self.client.login(password)['status'],200);self.assertTrue(self.read('SELECT password FROM users')[0]['password'].startswith('pbkdf2_sha256$600000$'))
 def test_login_throttling_persists_across_application_restart(self):
  for _ in range(5):self.assertEqual(self.client.login('wrong-password')['status'],401)
  limited=Client(create_app(self.settings,lambda:self.now));response=limited.login();self.assertEqual(response['status'],429);self.assertIn('Retry-After',response['headers']);self.now+=901;self.assertEqual(limited.login()['status'],200)
 def test_missing_account_uses_same_login_error(self):
  a=self.client.login('wrong-password');b=self.client.request('/api/login',{'username':'missing','password':'wrong-password'});self.assertEqual(a['body'],b['body'])
 def test_session_tokens_are_hashed_rotated_and_csrf_changes(self):
  self.assertEqual(self.client.login()['status'],200);old_cookie=self.client.cookie;old_csrf=self.client.csrf
  stored=self.read('SELECT token FROM sessions')[0]['token'];self.assertNotIn(stored,old_cookie);self.assertEqual(self.client.login()['status'],200);self.assertNotEqual(self.client.cookie,old_cookie);self.assertNotEqual(self.client.csrf,old_csrf)
  other=Client(self.app);other.cookie=old_cookie;self.assertEqual(other.request('/api/state')['status'],401)
 def test_session_idle_timeout(self):
  self.client.login();self.now+=1801;self.assertEqual(self.client.request('/api/state')['status'],401)
 def test_session_absolute_timeout_even_with_activity(self):
  self.client.login()
  for _ in range(17):self.now+=1700;self.assertIn(self.client.request('/api/session')['status'],[200,401])
  self.assertEqual(self.client.request('/api/state')['status'],401)
 def test_host_origin_csrf_and_proxy_spoof_rejected(self):
  self.client.login()
  for path,headers in [('/api/state',{'Host':'evil.example'}),('/api/customers',{'Origin':'https://evil.example'}),('/api/customers',{'Sec-Fetch-Site':'cross-site'}),('/api/customers',{'X-CSRF-Token':'wrong'})]:
   response=self.client.request(path,{'name':'DEMO','address':'DEMO'} if path!='/api/state' else None,headers=headers);self.assertEqual(response['status'],403)
  app,client=self.production();response=client.request('/api/session',scheme='http',headers={'X-Forwarded-Proto':'https'});self.assertEqual(response['status'],403)
 def test_private_files_unknown_methods_and_json_limits(self):
  for path in ['/server.py','/data/erp.sqlite3','/.env','/.git/config','/../security.py']:self.assertEqual(self.client.request(path)['status'],404)
  self.assertEqual(self.client.request('/',method='DELETE')['status'],405)
  self.assertEqual(self.client.request('/api/login',raw=b'bad-json')['status'],400)
  self.assertEqual(self.client.request('/api/login',{},headers={'Content-Type':'text/plain'})['status'],415)
  self.assertEqual(self.client.request('/api/login',raw=b'x'*500001)['status'],413)
 def test_security_headers_on_static_and_api_errors(self):
  for path in ['/','/api/state']:
   headers=self.client.request(path)['headers'];self.assertEqual(headers['X-Frame-Options'],'DENY');self.assertIn("object-src 'none'",headers['Content-Security-Policy']);self.assertEqual(headers['Referrer-Policy'],'no-referrer');self.assertIn('X-Request-ID',headers)
  head=self.client.request('/',method='HEAD');self.assertEqual(head['raw'],b'');self.assertGreater(int(head['headers']['Content-Length']),0)
 def test_production_configuration_fails_closed(self):
  with self.assertRaises(ValueError):Settings(production=True,public_origin='http://erp.example.test').validate()
  with self.assertRaises(ValueError):Settings(bind='0.0.0.0').validate()
  with self.assertRaises(ValueError):Settings(trusted_proxy='*').validate()
  with self.assertRaises(ValueError):Settings(setup_token='short').validate()
 def test_production_bootstrap_token_and_no_second_admin(self):
  with core.database(True) as c:c.execute('DELETE FROM users')
  cfg=Settings(production=True,public_origin='https://erp.example.test',mfa_key=Fernet.generate_key().decode())
  with self.assertRaises(ValueError):create_app(cfg)
  app,client=self.production();self.assertTrue(client.request('/api/session')['body']['setup_token_required']);payload={'name':'DEMO admin','username':'demo-admin','password':'Demo-admin-password-2026'}
  self.assertEqual(client.request('/api/setup',{**payload,'setup_token':'wrong'})['status'],401);self.assertFalse(self.read('SELECT * FROM users'))
  response=client.request('/api/setup',{**payload,'setup_token':app.settings.setup_token});self.assertEqual(response['status'],200);self.assertTrue(response['body']['mfa_enrollment_required']);self.assertEqual(client.request('/api/setup',{**payload,'setup_token':app.settings.setup_token})['status'],409)
 def test_admin_mfa_gate_secure_cookie_and_encrypted_secret(self):
  app,client=self.production();login=client.login();self.assertEqual(login['status'],200);self.assertTrue(login['body']['mfa_enrollment_required']);self.assertIn('__Host-erp_session=',login['headers']['Set-Cookie']);self.assertIn('; Secure',login['headers']['Set-Cookie']);self.assertIn('Strict-Transport-Security',login['headers']);self.assertEqual(client.request('/api/state')['status'],403);self.assertEqual(client.request('/api/products',{'name':'DEMO'})['status'],403)
  secret,codes=self.enroll(client);stored=self.read('SELECT mfa_secret,mfa_recovery FROM users')[0];self.assertNotEqual(stored['mfa_secret'],secret);self.assertNotIn(codes[0],stored['mfa_recovery']);self.assertEqual(client.request('/api/state')['status'],200)
 def test_mfa_totp_replay_and_single_use_recovery_codes(self):
  app,client=self.production();client.login();secret,codes=self.enroll(client);code=totp(secret,int(self.now//30));fresh=Client(app,True);self.assertEqual(fresh.login(code=code)['status'],401);self.now+=30;code=totp(secret,int(self.now//30));self.assertEqual(fresh.login(code=code)['status'],200);self.assertEqual(Client(app,True).login(code=code)['status'],401)
  recovery=Client(app,True);self.assertEqual(recovery.login(code=codes[0])['status'],200);self.assertEqual(Client(app,True).login(code=codes[0])['status'],401)
 def test_mfa_enrollment_expires_and_codes_are_rate_limited(self):
  self.client.login();self.client.request('/api/security/mfa/enroll',{'current_password':'Test-password-2026'});self.now+=601
  self.assertEqual(self.client.request('/api/security/mfa/confirm',{'code':'000000'})['status'],400)
  for _ in range(4):self.client.request('/api/security/mfa/confirm',{'code':'000000'})
  self.assertEqual(self.client.request('/api/security/mfa/confirm',{'code':'000000'})['status'],429)
 def test_password_change_revokes_other_devices(self):
  a=self.client;a.login();b=Client(self.app);b.login();old=a.cookie;response=a.request('/api/security/password',{'current_password':'Test-password-2026','new_password':'Changed-password-2026'});self.assertEqual(response['status'],200);self.assertNotEqual(a.cookie,old);self.assertEqual(b.request('/api/state')['status'],401);self.assertEqual(a.request('/api/state')['status'],200);self.assertEqual(Client(self.app).login()['status'],401);self.assertEqual(Client(self.app).login('Changed-password-2026')['status'],200)
 def test_role_change_revokes_sessions_and_does_not_leak_secrets(self):
  self.client.login();self.assertEqual(self.client.request('/api/state')['status'],200)
  self.client.request('/api/users',{'name':'DEMO viewer','username':'viewer','password':'Demo-viewer-2026','role':'Viewer'});viewer=Client(self.app);viewer.request('/api/login',{'username':'viewer','password':'Demo-viewer-2026'});row=self.read("SELECT id FROM users WHERE username='viewer'")[0]
  self.client.request('/api/users',{'id':row['id'],'name':'DEMO viewer','role':'Viewer','active':False});self.assertEqual(viewer.request('/api/state')['status'],401)
  state=self.client.request('/api/state');text=json.dumps(state['body']);self.assertNotIn('pbkdf2_sha256$',text);self.assertNotIn('mfa_secret',text)
 def test_logs_and_audit_never_contain_password_or_factor(self):
  self.client.login('Do-not-record-this-password');self.client.login();self.client.request('/api/security/mfa/enroll',{'current_password':'Test-password-2026'});audit=json.dumps(self.read('SELECT * FROM audit'));self.assertNotIn('Do-not-record-this-password',audit);self.assertNotIn('Test-password-2026',audit);self.assertNotIn('otpauth:',audit)
 def test_encrypted_backup_restore_and_no_overwrite(self):
  self.client.login();self.fixture.workflow();key=Fernet.generate_key();archive,counts=maintenance.make_backup(Path(self.fixture.tmp.name)/'backups',key);self.assertFalse(archive.read_bytes().startswith(b'SQLite format'));restored=Path(self.fixture.tmp.name)/'restore.sqlite3';self.assertEqual(maintenance.restore_backup(archive,restored,key)['pis'],counts['pis'])
  with closing(sqlite3.connect(restored)) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
  with self.assertRaises(ValueError):maintenance.restore_backup(archive,core.DB,key)
  with self.assertRaises(ValueError):maintenance.inspect_backup(archive,Fernet.generate_key())
  damaged=Path(self.fixture.tmp.name)/'damaged.fernet';damaged.write_bytes(archive.read_bytes()[:-10]+b'broken')
  with self.assertRaises(ValueError):maintenance.inspect_backup(damaged,key)
 def test_secret_generation_does_not_replace_keys(self):
  folder=Path(self.fixture.tmp.name)/'keys';paths=maintenance.generate_secrets(folder);before=[Path(p).read_bytes() for p in paths]
  with self.assertRaises(ValueError):maintenance.generate_secrets(folder)
  self.assertEqual(before,[Path(p).read_bytes() for p in paths])

 def test_mfa_replacement_preserves_protection_until_confirmed(self):
  self.client.login();old_secret,old_codes=self.enroll(self.client);old_cipher=self.read('SELECT mfa_secret FROM users')[0]['mfa_secret'];self.now+=30
  started=self.client.request('/api/security/mfa/enroll',{'current_password':'Test-password-2026','code':old_codes[0]});self.assertEqual(started['status'],200);self.assertEqual(self.read('SELECT mfa_secret FROM users')[0]['mfa_secret'],old_cipher)
  new_secret=started['body']['secret'];confirmed=self.client.request('/api/security/mfa/confirm',{'code':totp(new_secret,int(self.now//30))});self.assertEqual(confirmed['status'],200);self.assertNotEqual(self.read('SELECT mfa_secret FROM users')[0]['mfa_secret'],old_cipher)
  fresh=Client(self.app);self.assertEqual(fresh.login(code=old_codes[1])['status'],401);self.assertEqual(fresh.login(code=confirmed['body']['recovery_codes'][0])['status'],200)
 def test_production_user_changes_require_fresh_admin_verification(self):
  app,client=self.production();client.login();secret,codes=self.enroll(client);payload={'name':'DEMO extra','username':'extra','password':'Demo-extra-password-2026','role':'Viewer'}
  self.assertEqual(client.request('/api/users',payload)['status'],403);self.now+=30
  verified={**payload,'actor_password':'Test-password-2026','actor_code':totp(secret,int(self.now//30))};headers={'Idempotency-Key':'demo-admin-user-write'};a=client.request('/api/users',verified,headers=headers);b=client.request('/api/users',verified,headers=headers);self.assertEqual(a['status'],200);self.assertEqual(a['body'],b['body'])
 def test_idempotency_cache_never_stores_raw_passwords(self):
  self.client.login();password='Demo-viewer-secret-2026';response=self.client.request('/api/users',{'name':'DEMO','username':'demo','password':password,'role':'Viewer'});self.assertEqual(response['status'],200)
  cached=self.read('SELECT payload FROM requests');self.assertTrue(cached);self.assertTrue(all(row['payload'].startswith('hmac-sha256:') for row in cached));self.assertNotIn(password,json.dumps(cached))
 def test_legacy_request_cache_removed_without_business_data_loss(self):
  self.fixture.workflow();before=self.read('SELECT id,number,total FROM pis')
  with core.database(True) as c:c.execute('INSERT INTO requests VALUES(1,?,?,?,?)',('old-key','/api/users','{"password":"DEMO old secret"}','{}'))
  core.initialize();self.assertEqual(self.read('SELECT id,number,total FROM pis'),before);self.assertFalse(self.read("SELECT * FROM requests WHERE key='old-key'"))
 def test_wrong_mfa_key_blocks_startup(self):
  self.client.login();self.enroll(self.client)
  with self.assertRaises(ValueError):self.production()
 def test_concurrent_sign_in_failures_cannot_bypass_limit(self):
  import threading
  statuses=[]
  def attempt():statuses.append(Client(self.app).login('wrong-password')['status'])
  threads=[threading.Thread(target=attempt) for _ in range(6)]
  for thread in threads:thread.start()
  for thread in threads:thread.join()
  self.assertEqual(statuses.count(401),5);self.assertEqual(statuses.count(429),1)
 def test_backup_restore_supports_pre_security_user_schema(self):
  with core.database(True) as c:
   for column in ['mfa_secret','mfa_pending','mfa_pending_until','mfa_last_counter','mfa_recovery']:c.execute('ALTER TABLE users DROP COLUMN '+column)
  key=Fernet.generate_key();archive,counts=maintenance.make_backup(Path(self.fixture.tmp.name)/'legacy-backups',key);target=Path(self.fixture.tmp.name)/'legacy-restored.sqlite3';self.assertEqual(maintenance.restore_backup(archive,target,key)['users'],1)
if __name__=='__main__':unittest.main(verbosity=2)

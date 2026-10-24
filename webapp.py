"""WSGI HTTP boundary. Business calculations remain in server.py."""
import json,logging,os,secrets,sqlite3,time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit
import server as core
from security import Settings,Authentication,identity,public_user,session_cookie,cookie_token,token_hash

MAX_BODY=500000
CSP="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
class Application:
 def __init__(self,settings=None,clock=time.time):
  self.settings=settings or Settings.from_env();self.settings.validate();self.clock=clock;self.auth=Authentication(self.settings,clock);self.auth.guard_bootstrap()
 def _boundary(self,env):
  cfg=self.settings;host=env.get('HTTP_HOST','');scheme=env.get('wsgi.url_scheme','http');path=env.get('PATH_INFO','/');peer=env.get('REMOTE_ADDR','')
  if path=='/healthz' and peer in ['127.0.0.1','::1'] and host in ['localhost:'+env.get('SERVER_PORT',''),'127.0.0.1:'+env.get('SERVER_PORT','')]:return
  if cfg.production:
   if host.lower()!=urlsplit(cfg.public_origin).netloc.lower():raise core.RuleError('Host is not allowed',403)
   if scheme!='https':raise core.RuleError('HTTPS is required',403)
  else:
   if peer not in ['127.0.0.1','::1']:raise core.RuleError('Local workspace access only',403)
   if host not in ['127.0.0.1:'+env.get('SERVER_PORT',''),'localhost:'+env.get('SERVER_PORT',''),'[::1]:'+env.get('SERVER_PORT','')]:raise core.RuleError('Local workspace host required',403)
  if env.get('REQUEST_METHOD')=='POST':
   expected=cfg.public_origin if cfg.production else scheme+'://'+host
   origin=env.get('HTTP_ORIGIN')
   if origin is not None and origin!=expected:raise core.RuleError('Cross-origin requests are not allowed',403)
   if env.get('HTTP_SEC_FETCH_SITE')=='cross-site':raise core.RuleError('Cross-site requests are not allowed',403)
 def _body(self,env):
  if env.get('CONTENT_TYPE','').split(';')[0].strip().lower()!='application/json':raise core.RuleError('Content-Type must be application/json',415)
  try:length=int(env.get('CONTENT_LENGTH','0'))
  except ValueError:raise core.RuleError('Invalid request size')
  if not 0<length<=MAX_BODY:raise core.RuleError('Request body is too large or empty',413)
  raw=env['wsgi.input'].read(length)
  if len(raw)!=length:raise core.RuleError('Incomplete request body')
  try:data=json.loads(raw)
  except (UnicodeDecodeError,ValueError,RecursionError):raise core.RuleError('Invalid JSON body')
  if not isinstance(data,dict):raise core.RuleError('JSON object required')
  return data
 def dispatch(self,env):
  self._boundary(env);method=env.get('REQUEST_METHOD','GET');path=env.get('PATH_INFO','/');cfg=self.settings;now=self.clock();cookie=env.get('HTTP_COOKIE','');peer=env.get('REMOTE_ADDR','')
  if method not in ['GET','HEAD','POST']:raise core.RuleError('Method not allowed',405)
  if path=='/healthz' and method in ['GET','HEAD']:return 200,{'ok':True},None,None
  if method in ['GET','HEAD']:
   if path.startswith('/api/'):
    with core.database(True) as c:
     if path=='/api/session' and not c.execute('SELECT 1 FROM users').fetchone():return 200,{'setup_required':True,'setup_token_required':bool(cfg.setup_token)},None,None
     user=identity(c,cookie,cfg,now);limited=cfg.production and user['role']=='Admin' and not user['mfa_secret']
     if path=='/api/session':return 200,{'user':public_user(user),'csrf':user['csrf'],'mfa_enrollment_required':limited},None,None
     if limited:raise core.RuleError('Enable authenticator verification before accessing the workspace',403,mfa_enrollment_required=True)
     if path=='/api/state':
      state=core.state(c,user);state['security']['admin_verification_required']=cfg.production;return 200,state,None,None
     raise core.RuleError('Not found',404)
   files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
   if path not in files:raise core.RuleError('Not found',404)
   file=core.ROOT/'dist'/files[path]
   if not file.is_file():raise core.RuleError('Not found',404)
   content_type={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[file.suffix[1:]]
   return 200,file.read_bytes(),content_type,None
  if not path.startswith('/api/'):raise core.RuleError('Not found',404)
  data=self._body(env)
  if path in ['/api/login','/api/setup']:
   result,new_cookie=self.auth.sign_in(data,peer,cookie,path=='/api/setup');return 200,result,None,new_cookie
  security_route=path.startswith('/api/security/');sensitive=security_route or (cfg.production and path=='/api/users')
  try:
   with core.database(True) as c:
    user=identity(c,cookie,cfg,now)
    if not secrets.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),user['csrf']):raise core.RuleError('Session token mismatch',403)
    limited=cfg.production and user['role']=='Admin' and not user['mfa_secret']
    if limited and path not in ['/api/security/mfa/enroll','/api/security/mfa/confirm','/api/logout']:raise core.RuleError('Enable authenticator verification first',403,mfa_enrollment_required=True)
    if path=='/api/logout':
     # Revoke only this device; password/access changes revoke every device.
     c.execute('DELETE FROM sessions WHERE token=?',(token_hash(cookie_token(cookie,cfg)),));return 200,{'ok':True},None,session_cookie('',cfg,True)
    if sensitive:self.auth._check_limits(c,user['username'],peer,now)
    if security_route:
     if path=='/api/security/mfa/enroll':return 200,self.auth.enroll(c,user,data),None,None
     if path=='/api/security/mfa/confirm':result,new_cookie=self.auth.confirm(c,user,data,cookie);return 200,result,None,new_cookie
     if path=='/api/security/password':result,new_cookie=self.auth.change_password(c,user,data,cookie);return 200,result,None,new_cookie
     raise core.RuleError('Not found',404)
    key=env.get('HTTP_IDEMPOTENCY_KEY','')
    if not re_save_key(key):raise core.RuleError('A unique save key of at most 120 characters is required')
    cached=c.execute('SELECT * FROM requests WHERE user_id=? AND key=?',(user['id'],key)).fetchone();payload=self.auth.fingerprint(data)
    if cached:
     if cached['route']!=path or cached['payload']!=payload:raise core.RuleError('Save key reused with different data',409)
     result=json.loads(cached['response'])
    else:
     if cfg.production and path=='/api/users':
      core.authorize(user,'admin')
      if not core.verify(data.get('actor_password',''),user['password']) or not self.auth.verify_factor(c,user,data.get('actor_code',''),now):raise core.RuleError('Current Admin password and a fresh verification code are required',403)
     result=core.mutate(c,path,data,user);c.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(user['id'],key,path,payload,core.dumps(result)))
    return 200,result,None,None
  except core.RuleError as error:
   if sensitive and error.status in [400,403] and 'user' in locals():
    with core.database(True) as c:self.auth._failure(c,user['username'],peer,now)
   raise
 def __call__(self,env,start_response):
  request_id=secrets.token_hex(8);extras=[]
  try:status,data,content_type,cookie=self.dispatch(env)
  except core.RuleError as e:
   status,data,content_type,cookie=e.status,{'error':e.message,**e.details},None,None
   if status==429:extras.append(('Retry-After',str(e.details.get('retry_after',900))))
   if status==405:extras.append(('Allow','GET, HEAD, POST'))
  except sqlite3.IntegrityError:status,data,content_type,cookie=409,{'error':'Duplicate record or conflicting update. Nothing was partially saved.'},None,None
  except (ValueError,KeyError,TypeError,ArithmeticError,RecursionError):status,data,content_type,cookie=400,{'error':'Invalid or missing input.'},None,None
  except Exception as e:
   logging.getLogger('erp.security').error('Request %s failed (%s)',request_id,type(e).__name__);status,data,content_type,cookie=500,{'error':'Unable to complete this action; changes were rolled back.','request_id':request_id},None,None
  raw=data if isinstance(data,bytes) else core.dumps(data).encode('utf-8');headers=[('Content-Type',content_type or 'application/json; charset=utf-8'),('Content-Length',str(len(raw))),('Cache-Control','no-store' if env.get('PATH_INFO','').startswith('/api/') else 'no-cache'),('Content-Security-Policy',CSP),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Permissions-Policy','camera=(), microphone=(), geolocation=()'),('X-Robots-Tag','noindex, nofollow'),('X-Request-ID',request_id)]
  if self.settings.production and env.get('wsgi.url_scheme')=='https':headers.append(('Strict-Transport-Security','max-age=31536000'))
  if cookie:headers.append(('Set-Cookie',cookie))
  headers+=extras;start_response(str(status)+' '+HTTPStatus(status).phrase,headers)
  return [b'' if env.get('REQUEST_METHOD')=='HEAD' else raw]

def re_save_key(value):return isinstance(value,str) and 1<=len(value)<=120 and all(32<=ord(c)<127 for c in value)
def create_app(settings=None,clock=time.time):return Application(settings,clock)
def create_http_server(application,host='127.0.0.1',port=0):
 from waitress import create_server
 cfg=application.settings
 args=dict(host=host,port=port,threads=8,max_request_body_size=MAX_BODY,max_request_header_size=16384,channel_timeout=30,cleanup_interval=5,connection_limit=100,expose_tracebacks=False,clear_untrusted_proxy_headers=True)
 if cfg.trusted_proxy:args.update(trusted_proxy=cfg.trusted_proxy,trusted_proxy_headers={'x-forwarded-proto','x-forwarded-for'},trusted_proxy_count=1)
 return create_server(application,**args)
def close_http_server(http):
 http.task_dispatcher.shutdown(cancel_pending=True,timeout=5)
 for channel in list(http.active_channels.values()):channel.close()
 http.close()
def main():
 cfg=Settings.from_env();os.umask(0o077);core.initialize();application=create_app(cfg);http=create_http_server(application,cfg.bind,cfg.port)
 print('Satata ERP starting in '+('production' if cfg.production else 'local')+' mode on '+cfg.bind+':'+str(http.effective_port),flush=True)
 try:http.run()
 except KeyboardInterrupt:pass
 finally:close_http_server(http)
if __name__=='__main__':main()

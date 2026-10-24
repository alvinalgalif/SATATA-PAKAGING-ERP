"""Authentication controls shared by the WSGI application and security tests."""
import base64,hashlib,hmac,ipaddress,json,os,re,secrets,struct,time
from dataclasses import dataclass
from http.cookies import SimpleCookie,CookieError
from pathlib import Path
from urllib.parse import urlsplit,quote
import server as core
from cryptography.fernet import Fernet,InvalidToken

@dataclass(frozen=True)
class Settings:
 production:bool=False
 public_origin:str=''
 setup_token:str=''
 mfa_key:str=''
 idle_seconds:int=1800
 absolute_seconds:int=28800
 attempt_window:int=900
 account_limit:int=5
 ip_limit:int=50
 bind:str='127.0.0.1'
 port:int=8765
 trusted_proxy:str=''
 @classmethod
 def from_env(cls):
  mode=os.getenv('ERP_ENV','local')
  if mode not in ['local','production']:raise ValueError('ERP_ENV must be local or production')
  prod=mode=='production';origin=os.getenv('ERP_PUBLIC_ORIGIN','').rstrip('/')
  settings=cls(production=prod,public_origin=origin,setup_token=os.getenv('ERP_SETUP_TOKEN',''),mfa_key=os.getenv('ERP_MFA_KEY',''),bind=os.getenv('ERP_BIND','127.0.0.1'),port=int(os.getenv('PORT','8765')),trusted_proxy=os.getenv('ERP_TRUSTED_PROXY',''))
  settings.validate();return settings
 def validate(self):
  if self.production:
   u=urlsplit(self.public_origin)
   if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:raise ValueError('Production requires ERP_PUBLIC_ORIGIN=https://your-exact-host')
   if not self.mfa_key:raise ValueError('Production requires ERP_MFA_KEY from the secret store')
   Fernet(self.mfa_key.encode())
   if not core.DB.is_absolute() or core.DB.resolve().is_relative_to(core.ROOT.resolve()):raise ValueError('Production ERP_DB must be an absolute path on persistent storage outside the source directory')
  elif self.bind not in ['127.0.0.1','::1','localhost']:raise ValueError('Local mode may only bind to loopback')
  if self.trusted_proxy:ipaddress.ip_address(self.trusted_proxy)
  if self.setup_token and len(self.setup_token)<32:raise ValueError('ERP_SETUP_TOKEN must contain at least 32 random characters')
  if self.idle_seconds<=0 or self.absolute_seconds<self.idle_seconds:raise ValueError('Invalid session lifetimes')
 @property
 def cookie_name(self):return '__Host-erp_session' if self.production else 'erp_session'

def local_key(name):
 path=core.DB.parent/name;path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():
  try:
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
  except FileExistsError:pass
 return path.read_bytes().strip()

def public_user(user):return {k:user[k] for k in ['id','name','username','role']}
def cookie_token(header,settings):
 jar=SimpleCookie()
 try:jar.load(header or '')
 except CookieError:return ''
 return jar[settings.cookie_name].value if settings.cookie_name in jar else ''
def token_hash(value):return hashlib.sha256(value.encode()).hexdigest()
def session_cookie(token,settings,clear=False):
 value=settings.cookie_name+'='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age='+('0' if clear else str(settings.absolute_seconds))
 return value+('; Secure' if settings.production else '')
def issue_session(c,user,settings,now,mfa_verified=False,old_token=''):
 if old_token:c.execute('DELETE FROM sessions WHERE token=?',(token_hash(old_token),))
 c.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<=?',(now,now-settings.idle_seconds))
 token=secrets.token_hex(32);csrf=secrets.token_hex(24)
 c.execute('INSERT INTO sessions(token,user_id,csrf,expires,created,last_seen,mfa_verified) VALUES(?,?,?,?,?,?,?)',(token_hash(token),user['id'],csrf,now+settings.absolute_seconds,now,now,int(mfa_verified)))
 return {'ok':True,'csrf':csrf,'user':public_user(user),'mfa_enrollment_required':bool(settings.production and user['role']=='Admin' and not user.get('mfa_secret'))},session_cookie(token,settings)
def identity(c,header,settings,now):
 token=cookie_token(header,settings)
 if not token:raise core.RuleError('Please sign in',401)
 row=c.execute('SELECT u.*,s.csrf,s.created session_created,s.last_seen,s.expires,s.mfa_verified FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1',(token_hash(token),)).fetchone()
 if not row:raise core.RuleError('Please sign in',401)
 user=dict(row)
 if user['expires']<=now or user['last_seen']+settings.idle_seconds<=now:raise core.RuleError('Your session expired. Please sign in again.',401)
 if user['mfa_secret'] and not user['mfa_verified']:raise core.RuleError('Sign in with your authenticator code',401)
 c.execute('UPDATE sessions SET last_seen=? WHERE token=?',(now,token_hash(token)))
 return user

def totp(secret,counter):
 raw=base64.b32decode(secret);digest=hmac.new(raw,struct.pack('>Q',counter),hashlib.sha1).digest();offset=digest[-1]&15
 return str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
def match_counter(secret,code,now,last=-1):
 if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):return None
 for counter in [int(now//30)+i for i in [-1,0,1]]:
  if counter>last and hmac.compare_digest(totp(secret,counter),code):return counter
 return None

class Authentication:
 def __init__(self,settings,clock=time.time):
  self.settings=settings;self.clock=clock
  key=settings.mfa_key.encode() if settings.mfa_key else local_key('mfa.key')
  self.cipher=Fernet(key);self.request_key=hashlib.sha256(b'erp-request-fingerprint\0'+key).digest()
  self.dummy=core.hash_password(secrets.token_urlsafe(24))
 def guard_bootstrap(self):
  with core.database() as c:
   empty=not c.execute('SELECT 1 FROM users').fetchone()
   if self.settings.production and empty and not self.settings.setup_token:raise ValueError('Set ERP_SETUP_TOKEN in the secret store before starting an empty production workspace')
   for row in c.execute("SELECT mfa_secret FROM users WHERE mfa_secret!=''"):
    try:self.cipher.decrypt(row['mfa_secret'].encode())
    except InvalidToken:raise ValueError('The configured MFA key does not decrypt existing authenticator secrets; restore the original key')
 def fingerprint(self,data):return 'hmac-sha256:'+hmac.new(self.request_key,core.dumps(data).encode(),hashlib.sha256).hexdigest()
 def _keys(self,username,peer):return [('account',token_hash(username.casefold())),('ip',token_hash(peer))]
 def _check_limits(self,c,username,peer,now):
  c.execute('DELETE FROM auth_attempts WHERE window_start<=?',(now-self.settings.attempt_window,))
  for kind,key in self._keys(username,peer):
   row=c.execute('SELECT attempts,window_start FROM auth_attempts WHERE kind=? AND key=?',(kind,key)).fetchone();limit=self.settings.account_limit if kind=='account' else self.settings.ip_limit
   if row and row['attempts']>=limit:raise core.RuleError('Too many sign-in attempts. Try again later.',429,retry_after=max(1,int(row['window_start']+self.settings.attempt_window-now)))
 def _failure(self,c,username,peer,now):
  for kind,key in self._keys(username,peer):c.execute('INSERT INTO auth_attempts VALUES(?,?,?,1) ON CONFLICT(kind,key) DO UPDATE SET attempts=attempts+1',(kind,key,now))
  core.audit(c,{'name':'Unauthenticated'},'Sign-in failed','authentication',{'account_hash':token_hash(username.casefold()),'peer_hash':token_hash(peer)})
 def verify_factor(self,c,user,code,now):
  if not user['mfa_secret']:return False
  secret=self.cipher.decrypt(user['mfa_secret'].encode()).decode();counter=match_counter(secret,code,now,user['mfa_last_counter'])
  if counter is not None:c.execute('UPDATE users SET mfa_last_counter=? WHERE id=?',(counter,user['id']));return True
  recovery=json.loads(user['mfa_recovery']);digest=token_hash(str(code).strip())
  for existing in recovery:
   if hmac.compare_digest(existing,digest):
    recovery.remove(existing);c.execute('UPDATE users SET mfa_recovery=? WHERE id=?',(json.dumps(recovery),user['id']));core.audit(c,user,'MFA recovery code used',user['id'],{});return True
  return False
 def sign_in(self,data,peer,old_cookie='',setup=False):
  username=data.get('username','');password=data.get('password','');now=self.clock();failure=False
  if not isinstance(username,str) or not 1<=len(username)<=128 or not isinstance(password,str) or len(password)>4096:raise core.RuleError('Invalid sign-in details',400)
  with core.database(True) as c:
   self._check_limits(c,username,peer,now)
   if setup:
    if c.execute('SELECT 1 FROM users').fetchone():raise core.RuleError('Setup has already been completed',409)
    supplied=data.get('setup_token','')
    if self.settings.setup_token and (not isinstance(supplied,str) or not hmac.compare_digest(supplied,self.settings.setup_token)):
     self._failure(c,username,peer,now);failure=True
    else:
     if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,128}',username):raise core.RuleError('Username must be 3-128 letters, numbers or . _ @ -')
     c.execute('INSERT INTO users(name,username,password,role) VALUES(?,?,?,?)',(core.required(data,'name'),username,core.hash_password(password),'Admin'))
   user_row=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone();user=dict(user_row) if user_row else None
   password_ok=core.verify(password,user['password'] if user else self.dummy)
   factor_ok=bool(user and (not user['mfa_secret'] or self.verify_factor(c,user,data.get('code',''),now))) if password_ok else False
   if failure or not password_ok or not factor_ok:
    if not failure:self._failure(c,username,peer,now)
    failure=True
   else:
    # Upgrade a supported old hash without invalidating the user's existing password.
    if not user['password'].startswith('pbkdf2_sha256$600000$'):
     salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex();c.execute('UPDATE users SET password=? WHERE id=?',('pbkdf2_sha256$600000$'+salt+'$'+digest,user['id']))
    c.execute('DELETE FROM auth_attempts WHERE kind=? AND key=?',('account',token_hash(username.casefold())))
    core.audit(c,user,'Signed in',user['id'],{})
    result=issue_session(c,user,self.settings,now,bool(user['mfa_secret']),cookie_token(old_cookie,self.settings))
  if failure:raise core.RuleError('Invalid username, password or verification code',401)
  return result
 def enroll(self,c,user,data):
  if not core.verify(data.get('current_password',''),user['password']):raise core.RuleError('Current password is incorrect',403)
  if user['mfa_secret'] and not self.verify_factor(c,user,data.get('code',''),self.clock()):raise core.RuleError('A fresh authenticator or unused recovery code is required',403)
  secret=base64.b32encode(secrets.token_bytes(20)).decode();cipher=self.cipher.encrypt(secret.encode()).decode()
  c.execute('UPDATE users SET mfa_pending=?,mfa_pending_until=? WHERE id=?',(cipher,self.clock()+600,user['id']))
  core.audit(c,user,'MFA enrollment started',user['id'],{})
  uri='otpauth://totp/'+quote('Satata ERP:'+user['username'],safe='')+'?secret='+secret+'&issuer=Satata%20ERP&algorithm=SHA1&digits=6&period=30'
  return {'secret':secret,'uri':uri}
 def confirm(self,c,user,data,cookie):
  now=self.clock()
  if not user['mfa_pending'] or user['mfa_pending_until']<now:raise core.RuleError('Authenticator setup expired. Start again.')
  secret=self.cipher.decrypt(user['mfa_pending'].encode()).decode();counter=match_counter(secret,data.get('code',''),now)
  if counter is None:raise core.RuleError('Invalid authenticator code')
  codes=[secrets.token_hex(8) for _ in range(10)];hashes=[token_hash(code) for code in codes]
  c.execute('UPDATE users SET mfa_secret=?,mfa_pending=?,mfa_pending_until=0,mfa_last_counter=?,mfa_recovery=? WHERE id=?',(user['mfa_pending'],'',counter,json.dumps(hashes),user['id']))
  c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],));user=core.one(c,'users',user['id']);core.audit(c,user,'MFA enabled',user['id'],{})
  response,new_cookie=issue_session(c,user,self.settings,now,True);response['recovery_codes']=codes;return response,new_cookie
 def change_password(self,c,user,data,cookie):
  if not core.verify(data.get('current_password',''),user['password']):raise core.RuleError('Current password is incorrect',403)
  if user['mfa_secret'] and not self.verify_factor(c,user,data.get('code',''),self.clock()):raise core.RuleError('A fresh authenticator or recovery code is required',403)
  password=data.get('new_password','')
  if core.verify(password,user['password']):raise core.RuleError('Choose a different password')
  c.execute('UPDATE users SET password=? WHERE id=?',(core.hash_password(password),user['id']));c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],));core.audit(c,user,'Password changed',user['id'],{})
  return issue_session(c,user,self.settings,self.clock(),bool(user['mfa_secret']))

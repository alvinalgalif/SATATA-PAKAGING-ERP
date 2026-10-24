import json,os,sqlite3,secrets,hashlib,hmac,time,re
from decimal import Decimal,ROUND_HALF_UP
from datetime import date,datetime,timezone
from pathlib import Path
import sys
from contextlib import contextmanager
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
if (ROOT/'.runtime-deps').is_dir():sys.path.insert(0,str(ROOT/'.runtime-deps'))
DB=Path(os.environ.get('ERP_DB',str(ROOT/'data'/'erp.sqlite3')))
D=Decimal
ROLES={'Admin':{'admin','pi','approve','recipe','stock','finance','customer'},'Sales':{'pi','customer'},'Approver':{'approve'},'Manufacturing':{'recipe'},'Inventory':{'stock'},'Finance':{'finance'},'Viewer':set()}
class RuleError(Exception):
 def __init__(self,message,status=400,**details):self.message=message;self.status=status;self.details=details
def num(v,name='Number',zero=False):
 try:x=D(str(v))
 except Exception:raise RuleError(name+' must be numeric')
 if not x.is_finite() or x<0 or (not zero and x==0):raise RuleError(name+' must be '+('nonnegative' if zero else 'positive'))
 if x>D('1000000000000'):raise RuleError(name+' exceeds supported limit')
 return x
def dumps(x):return json.dumps(x,default=lambda v:format(v,'f'),separators=(',',':'))
def money(v,places='0.01'):return str(D(v).quantize(D(places),rounding=ROUND_HALF_UP))
def required(d,k):
 v=str(d.get(k,'')).strip()
 if not v or len(v)>2000:raise RuleError(k+' is required (maximum 2000 characters)')
 return v
def valid_date(v):
 try:return date.fromisoformat(str(v)).isoformat()
 except Exception:raise RuleError('A valid date is required')
def hash_password(p,salt=None):
 if not isinstance(p,str) or not 15<=len(p)<=128:raise RuleError('Use a password between 15 and 128 characters')
 if p.casefold() in {'passwordpassword','123456789012345','qwertyuiopasdfgh'}:raise RuleError('Choose a less predictable password')
 salt=salt or secrets.token_hex(16)
 return 'pbkdf2_sha256$600000$'+salt+'$'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),600000).hex()
def verify(p,hashed):
 if not isinstance(p,str) or not 1<=len(p)<=4096:return False
 try:
  if hashed.startswith('pbkdf2_sha256$'):
   algorithm,rounds,salt,digest=hashed.split('$');rounds=int(rounds)
   if not 250000<=rounds<=2000000:return False
  else:salt,digest=hashed.split(':');rounds=250000
  return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),rounds).hex(),digest)
 except (ValueError,TypeError):return False
@contextmanager
def database(write=False):
 DB.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA secure_delete=ON')
 try:
  if write:c.execute('BEGIN IMMEDIATE')
  yield c
  if write:c.commit()
 except Exception:c.rollback();raise
 finally:c.close()
def rows(c,q,args=()):return [dict(r) for r in c.execute(q,args)]
def one(c,table,id):
 r=c.execute('SELECT * FROM '+table+' WHERE id=?',(id,)).fetchone()
 if not r:raise RuleError('Record not found',404)
 return dict(r)
def audit(c,user,action,entity,detail):c.execute('INSERT INTO audit(actor,action,entity,detail,created) VALUES(?,?,?,?,?)',(user['name'],action,str(entity),dumps(detail),datetime.now(timezone.utc).isoformat()))
def setting(c,k):
 r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else None
def initialize():
 with database(True) as c:
  c.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,address TEXT NOT NULL,email TEXT,phone TEXT,contact TEXT);
  CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,hs_code TEXT NOT NULL DEFAULT '',unit TEXT NOT NULL DEFAULT 'PCS');
  CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY,product_id INTEGER REFERENCES products(id),version INTEGER NOT NULL,config TEXT NOT NULL,validated INTEGER NOT NULL DEFAULT 0,sample TEXT,created TEXT NOT NULL,UNIQUE(product_id,version));
  CREATE TABLE IF NOT EXISTS materials(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,unit TEXT NOT NULL DEFAULT 'KG');
  CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY,material_id INTEGER REFERENCES materials(id),boe TEXT NOT NULL,batch TEXT NOT NULL,received TEXT NOT NULL,quantity TEXT NOT NULL,unit_cost TEXT NOT NULL,currency TEXT NOT NULL,fx TEXT NOT NULL,UNIQUE(material_id,boe,batch));
  CREATE TABLE IF NOT EXISTS calculations(id TEXT PRIMARY KEY,recipe_id INTEGER REFERENCES recipes(id),inputs TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,user_id INTEGER REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS pis(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL,customer_id INTEGER REFERENCES customers(id),snapshot TEXT NOT NULL,total TEXT NOT NULL,currency TEXT NOT NULL,created TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS pi_versions(id INTEGER PRIMARY KEY,pi_id INTEGER REFERENCES pis(id),revision INTEGER NOT NULL,status TEXT NOT NULL,snapshot TEXT NOT NULL,total TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(pi_id,revision));
  CREATE TABLE IF NOT EXISTS lcs(id INTEGER PRIMARY KEY,pi_id INTEGER REFERENCES pis(id),number TEXT UNIQUE NOT NULL,status TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,details TEXT NOT NULL,created TEXT NOT NULL);
  CREATE UNIQUE INDEX IF NOT EXISTS one_final_lc ON lcs(pi_id) WHERE status='Confirmed';
  CREATE UNIQUE INDEX IF NOT EXISTS one_lc_per_pi ON lcs(pi_id);
  CREATE TABLE IF NOT EXISTS collections(id INTEGER PRIMARY KEY,lc_id INTEGER REFERENCES lcs(id),amount TEXT NOT NULL,date TEXT NOT NULL,reference TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY,pi_id INTEGER REFERENCES pis(id),batch_id INTEGER REFERENCES batches(id),quantity TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
  CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation ON reservations(pi_id,batch_id) WHERE active=1;
  CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY,batch_id INTEGER REFERENCES batches(id),kind TEXT NOT NULL,quantity TEXT NOT NULL,reference TEXT NOT NULL,created TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,entity TEXT,detail TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS requests(user_id INTEGER,key TEXT,route TEXT,payload TEXT,response TEXT,PRIMARY KEY(user_id,key));
  ''')
  migrations={'sessions':{'created':'REAL NOT NULL DEFAULT 0','last_seen':'REAL NOT NULL DEFAULT 0','mfa_verified':'INTEGER NOT NULL DEFAULT 0'},'users':{'mfa_secret':"TEXT NOT NULL DEFAULT ''",'mfa_pending':"TEXT NOT NULL DEFAULT ''",'mfa_pending_until':'REAL NOT NULL DEFAULT 0','mfa_last_counter':'INTEGER NOT NULL DEFAULT -1','mfa_recovery':"TEXT NOT NULL DEFAULT '[]'"}}
  for table,columns in migrations.items():
   existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
   for column,definition in columns.items():
    if column not in existing:c.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+definition)
  c.execute('DELETE FROM sessions WHERE created=0 OR expires<?',(time.time(),))
  c.execute("DELETE FROM requests WHERE payload NOT LIKE 'hmac-sha256:%'")
  c.execute('CREATE TABLE IF NOT EXISTS auth_attempts(kind TEXT,key TEXT,window_start REAL NOT NULL,attempts INTEGER NOT NULL,PRIMARY KEY(kind,key))')
  if not setting(c,'company'):
   c.execute('INSERT INTO settings VALUES(?,?)',('company',dumps({'name':'SATATA PACKAGING INDUSTRIES LTD','address':'Dhag No. 24353, Road # 04, Foujir Batan, Uttarkhan, Uttara, Dhaka-1230.','bank':'ISLAMI BANK BANGLADESH LTD','bank_address':'SONARGAON JANAPATH ROAD BRANCH, UTTARA, DHAKA-1230','swift':'IBBLBDDH383','account':'','prefix':'SPI','terms':'','shipment':'By Truck','loading':'Factory Premises of Beneficiary','destination':'Applicants Factory'})))
   c.execute('INSERT INTO settings VALUES(?,?)',('template',dumps({'title':'PROFORMA INVOICE','notes':''})))
  if not c.execute('SELECT 1 FROM materials').fetchone():
   for n in ['PLAIN LAYER LINER PAPER','CORRUGATED LAYER','STARECH / GLUE / GUM','DUPLEX BOARD','Duplex Board / Art Card','Printing INK']:c.execute('INSERT INTO materials(name) VALUES(?)',(n,))
   for n in ['MASTER CARTOON 3 PLY','MASTER CARTOON 5 PLY','MASTER CARTOON 7 PLY','BACK BOARD','NECK BOARD','TOP-BOTTOM 3 PLY','DEVIDER 3 PLY','CROSS DEVIDER 3 PLY','TOP-BOTTOM 5 PLY','DEVIDER 5 PLY','CROSS DEVIDER 5 PLY','TOP-BOTTOM 7 PLY','DEVIDER 7 PLY','CROSS DEVIDER 7 PLY','SHOE BOX / PHOTO (TWO PART)']:c.execute('INSERT INTO products(name) VALUES(?)',(n,))
   cfg={'basis':'per_piece','basis_pieces':'1','dimensions':{'L':{'parts':['l','w'],'allowance':'6'},'W':{'parts':['h','w'],'allowance':'2'}},'components':[{'material_id':1,'kind':'paper','gsm':'liner_gsm','axis1':'L','axis2':'W','layers':'2','multiplier':'2','corrugation':'0','wastage':'8','divisor':'10000000'},{'material_id':2,'kind':'paper','gsm':'medium_gsm','axis1':'L','axis2':'W','layers':'1','multiplier':'2','corrugation':'40','wastage':'8','divisor':'10000000'},{'material_id':3,'kind':'glue','grams_per_kg':'20','wastage':'5','sources':[1,2]}],'note':'Per-piece basis confirmed. Original x2x2 and x1x2 preserved; multiplier semantics not renamed. Glue from MANUFACTURING!J2 explicitly approved.','complete':True}
   c.execute('INSERT INTO recipes(product_id,version,config,created) VALUES(1,1,?,?)',(dumps(cfg),date.today().isoformat()))
def stock(c):
 result=[]
 for b in rows(c,'SELECT b.*,m.name material,m.unit FROM batches b JOIN materials m ON m.id=b.material_id ORDER BY received,b.id'):
  reserved=sum((D(r[0]) for r in c.execute('SELECT quantity FROM reservations WHERE batch_id=? AND active=1',(b['id'],))),D(0));b.update(on_hand=b['quantity'],reserved=str(reserved),available=str(D(b['quantity'])-reserved));result.append(b)
 return result
def requirements(config,inputs):
 values={k:num(inputs.get(k),k) for k in ['l','w','h','quantity']}
 gsm_labels={'liner_gsm':'Liner paper GSM','medium_gsm':'Medium paper GSM','gsm':'GSM'}
 used_gsm={comp['gsm'] for comp in config['components'] if comp['kind']=='paper'}
 for key in used_gsm:
  if key not in gsm_labels:raise RuleError('Unsupported GSM source in recipe')
  values[key]=num(inputs.get(key),gsm_labels[key]+' (required by recipe)')
 dims=dict(values)
 for axis,rule in config['dimensions'].items():
  dims[axis]=sum((values[x]*num(rule['factors'].get(x,0),'Dimension factor',True) for x in ['l','w','h']),D(0))+D(str(rule['allowance'])) if 'factors' in rule else sum((values[x] for x in rule['parts']),D(0))+D(str(rule['allowance']))
  if dims[axis]<=0:raise RuleError('Calculated dimension must be positive')
 result=[];base={}
 for comp in config['components']:
  if comp['kind']=='paper':
   raw=dims[comp['axis1']]*dims[comp['axis2']]*values[comp['gsm']]*num(comp['layers'],'Layers')*num(comp['multiplier'],'Multiplier')*(1+num(comp['corrugation'],'Corrugation',True)/100)/num(comp['divisor'],'Divisor');base[int(comp['material_id'])]=raw;per=raw*(1+num(comp['wastage'],'Wastage',True)/100)
  elif comp['kind']=='glue':
   if not comp['sources'] or any(x not in base for x in comp['sources']):raise RuleError('Glue sources must precede glue')
   per=sum((base[x] for x in comp['sources']),D(0))*num(comp['grams_per_kg'],'Glue grams per kg')/1000*(1+num(comp['wastage'],'Glue wastage',True)/100)
  elif comp['kind']=='fixed':per=num(comp['quantity'],'Material quantity')
  else:raise RuleError('Unsupported calculation step')
  basis=num(config.get('basis_pieces',1),'Basis pieces') if config['basis']=='batch' else D(1)
  result.append({'material_id':int(comp['material_id']),'per_piece':str(per/basis),'required':str(per/basis*values['quantity'])})
 if len({x['material_id'] for x in result})!=len(result):raise RuleError('Each material must have one calculation rule')
 return result,{k:str(dims[k]) for k in config['dimensions']}
def allocation(c,needs,exclude_pi=None,virtual=None):
 batches=stock(c);cost=D(0);shortages=[];details=[];virtual=virtual if virtual is not None else {}
 for need in needs:
  mid=need['material_id'];remaining=D(need['required']);allocated=[];material_cost=D(0)
  for b in batches:
   if b['material_id']!=mid:continue
   avail=D(b['available'])-virtual.get(b['id'],D(0))
   if exclude_pi:avail+=sum((D(x[0]) for x in c.execute('SELECT quantity FROM reservations WHERE pi_id=? AND batch_id=? AND active=1',(exclude_pi,b['id']))),D(0))
   take=min(remaining,max(D(0),avail))
   if take:
    unit=D(b['unit_cost'])*(D(b['fx']) if b['currency']=='USD' else 1);part=take*unit;cost+=part;material_cost+=part;remaining-=take;virtual[b['id']]=virtual.get(b['id'],D(0))+take;allocated.append({'batch_id':b['id'],'boe':b['boe'],'batch':b['batch'],'quantity':str(take),'cost_bdt':str(part),'unit_cost_bdt':str(unit)})
   if remaining==0:break
  material=one(c,'materials',mid)['name']
  if remaining>0:shortages.append({'material':material,'material_id':mid,'required':need['required'],'shortage':str(remaining)})
  details.append({**need,'material':material,'allocations':allocated,'cost_bdt':str(material_cost),'weighted_unit_cost_bdt':str(material_cost/(D(need['required'])-remaining)) if D(need['required'])>remaining else None})
 return details,shortages,cost
def calculate(c,inputs):
 recipe=one(c,'recipes',int(inputs['recipe_id']));cfg=json.loads(recipe['config']);needs,dims=requirements(cfg,inputs);currency=inputs.get('currency')
 if currency not in ['USD','BDT']:raise RuleError('Currency must be USD or BDT')
 fx=num(inputs.get('fx',1),'BDT per USD exchange rate');markup=num(inputs.get('markup',0),'Markup',True)
 virtual={}
 for calc_id in inputs.get('prior_calculations',[]):
  previous=json.loads(one(c,'calculations',calc_id)['result'])
  for material in previous['materials']:
   for a in material['allocations']:virtual[a['batch_id']]=virtual.get(a['batch_id'],D(0))+D(a['quantity'])
 details,shortages,cost=allocation(c,needs,inputs.get('pi_id'),virtual);ready=bool(recipe['validated'] and cfg.get('complete') and not shortages);unit=cost/num(inputs['quantity'])*(1+markup/100)/(fx if currency=='USD' else 1)
 return {'recipe_id':recipe['id'],'recipe_version':recipe['version'],'recipe_snapshot':cfg,'dimensions':dims,'materials':details,'shortages':shortages,'cost_bdt':str(cost),'unit_price':money(unit,'0.000001') if ready else None,'partial_unit_price':money(unit,'0.000001'),'currency':currency,'fx':str(fx),'ready':ready,'blockers':([] if recipe['validated'] else ['Validate this recipe using an independent sample first.'])+([] if cfg.get('complete') else ['Recipe components are incomplete.'])+(['Insufficient stock: all shortages are listed.'] if shortages else [])}
def authorize(user,permission):
 if permission not in ROLES.get(user['role'],set()):raise RuleError('Your role cannot perform this action',403)
def pi_snapshot(c,p):
 return {**p,'snapshot':json.loads(p['snapshot']),'history':[{**x,'snapshot':json.loads(x['snapshot'])} for x in rows(c,'SELECT * FROM pi_versions WHERE pi_id=? ORDER BY revision',(p['id'],))],'lc':rows(c,'SELECT id,number,status FROM lcs WHERE pi_id=?',(p['id'],))}
def state(c,user):
 lcs=[]
 for lc in rows(c,'SELECT l.*,p.number pi_number FROM lcs l JOIN pis p ON p.id=l.pi_id ORDER BY l.id DESC'):
  lc['details']=json.loads(lc['details']);lc['collections']=rows(c,'SELECT * FROM collections WHERE lc_id=?',(lc['id'],));paid=sum((D(x['amount']) for x in lc['collections']),D(0));lc['collected']=str(paid);lc['outstanding']=str(D(lc['amount'])-paid);lcs.append(lc)
 return {'user':{k:user[k] for k in ['id','name','username','role']},'permissions':list(ROLES[user['role']]),'security':{'mfa_enabled':bool(user.get('mfa_secret'))},'company':setting(c,'company'),'template':setting(c,'template'),'customers':rows(c,'SELECT * FROM customers ORDER BY name'),'products':rows(c,'SELECT * FROM products'),'materials':rows(c,'SELECT * FROM materials'),'recipes':[{**r,'config':json.loads(r['config']),'sample':json.loads(r['sample']) if r['sample'] else None} for r in rows(c,'SELECT * FROM recipes ORDER BY id DESC')],'batches':stock(c),'pis':[pi_snapshot(c,p) for p in rows(c,'SELECT * FROM pis ORDER BY id DESC')],'lcs':lcs,'movements':rows(c,'SELECT m.*,b.boe,b.batch,mt.name material FROM movements m JOIN batches b ON b.id=m.batch_id JOIN materials mt ON mt.id=b.material_id ORDER BY m.id DESC'),'reservations':rows(c,'SELECT r.*,p.number pi_number,b.boe,b.batch,m.name material FROM reservations r JOIN pis p ON p.id=r.pi_id JOIN batches b ON b.id=r.batch_id JOIN materials m ON m.id=b.material_id ORDER BY r.id DESC'),'audit':rows(c,'SELECT * FROM audit ORDER BY id DESC LIMIT 300') if user['role']=='Admin' else [],'users':rows(c,'SELECT id,name,username,role,active FROM users') if user['role']=='Admin' else [],'roles':{k:sorted(v) for k,v in ROLES.items()}}
def build_pi(c,d,user,old=None):
 customer=one(c,'customers',int(d['customer_id']));currency=d.get('currency');fx=num(d.get('fx',1),'Exchange rate');terms=required(d,'payment_terms');lines=[];virtual={};total=D(0)
 if not isinstance(d.get('lines'),list) or not d['lines'] or len(d['lines'])>50:raise RuleError('Add between 1 and 50 calculated lines')
 for line in d['lines']:
  calc=one(c,'calculations',line['calculation_id'])
  if calc['user_id']!=user['id'] and user['role']!='Admin':raise RuleError('Calculation belongs to another user',403)
  inputs=json.loads(calc['inputs']);result=json.loads(calc['result'])
  if not result['ready']:raise RuleError('Line needs a complete, validated calculation')
  if line.get('inputs')!=inputs:raise RuleError('Line inputs changed. Recalculate before saving.')
  if currency!=inputs['currency'] or fx!=D(str(inputs['fx'])):raise RuleError('Currency or exchange rate changed. Recalculate every line.')
  if time.time()-calc['created']>86400:raise RuleError('Calculation expired. Recalculate the line.')
  needs=[{k:v for k,v in x.items() if k in ['material_id','per_piece','required']} for x in result['materials']];details,shortages,_=allocation(c,needs,old['id'] if old else None,virtual)
  if shortages:raise RuleError('Insufficient stock across PI lines',shortages=shortages)
  original=[[(a['batch_id'],a['quantity'],a['unit_cost_bdt']) for a in x['allocations']] for x in result['materials']];current=[[(a['batch_id'],a['quantity'],a['unit_cost_bdt']) for a in x['allocations']] for x in details]
  if original!=current:raise RuleError('FIFO availability changed. Recalculate this line.')
  recipe=one(c,'recipes',calc['recipe_id']);product=one(c,'products',recipe['product_id']);qty=num(inputs['quantity']);price=D(result['unit_price']);amount=(qty*price).quantize(D('.01'),rounding=ROUND_HALF_UP);total+=amount
  lines.append({'product':product,'inputs':inputs,'calculation':result,'unit_price':str(price),'quantity':str(qty),'amount':str(amount),'hs_code':str(line.get('hs_code',product['hs_code']))})
 return {'customer':customer,'beneficiary':setting(c,'company'),'template':setting(c,'template'),'currency':currency,'fx':str(fx),'payment_terms':terms,'po_number':str(d.get('po_number','')),'date':valid_date(d['date']),'lines':lines,'notes':str(d.get('notes',''))},money(total)
def release_for_change(c,pi_id,now):
 for r in rows(c,'SELECT * FROM reservations WHERE pi_id=? AND active=1',(pi_id,)):
  c.execute('INSERT INTO movements(batch_id,kind,quantity,reference,created) VALUES(?,?,?,?,?)',(r['batch_id'],'Released',r['quantity'],'PI '+str(pi_id),now))
 c.execute('UPDATE reservations SET active=0 WHERE pi_id=?',(pi_id,))
def mutate(c,path,d,user):
 now=datetime.now(timezone.utc).isoformat()
 if path=='/api/customers':
  authorize(user,'customer');data=[required(d,'name'),required(d,'address'),str(d.get('email','')),str(d.get('phone','')),str(d.get('contact',''))]
  if d.get('id'):one(c,'customers',d['id']);c.execute('UPDATE customers SET name=?,address=?,email=?,phone=?,contact=? WHERE id=?',(*data,d['id']));id=d['id']
  else:id=c.execute('INSERT INTO customers(name,address,email,phone,contact) VALUES(?,?,?,?,?)',data).lastrowid
  audit(c,user,'Customer saved',id,{'name':d['name']});return {'id':id}
 if path=='/api/products':
  authorize(user,'recipe');name=required(d,'name');unit=str(d.get('unit','PCS')).strip().upper();hs_code=str(d.get('hs_code','')).strip()
  if unit not in ['PCS','DZN','ROLL','KG']:raise RuleError('Select PCS, DZN, ROLL or KG as the product counting unit')
  if d.get('id'):
   before=one(c,'products',int(d['id']));id=before['id']
   c.execute('UPDATE products SET name=?,hs_code=?,unit=? WHERE id=?',(name,hs_code,unit,id))
   audit(c,user,'Product updated',id,{'before':before,'after':one(c,'products',id)})
  else:
   id=c.execute('INSERT INTO products(name,hs_code,unit) VALUES(?,?,?)',(name,hs_code,unit)).lastrowid
   audit(c,user,'Product created',id,{'name':name,'hs_code':hs_code,'unit':unit})
  return {'id':id}
 if path=='/api/materials':
  authorize(user,'stock');id=c.execute('INSERT INTO materials(name) VALUES(?)',(required(d,'name'),)).lastrowid;audit(c,user,'Material created',id,d);return {'id':id}
 if path=='/api/stock-in':
  authorize(user,'stock');one(c,'materials',int(d['material_id']));currency=d.get('currency')
  if currency not in ['BDT','USD']:raise RuleError('Invalid currency')
  qty=num(d['quantity']);cost=num(d['unit_cost']);fx=num(d.get('fx',1),'Exchange rate');received=valid_date(d['received'])
  id=c.execute('INSERT INTO batches(material_id,boe,batch,received,quantity,unit_cost,currency,fx) VALUES(?,?,?,?,?,?,?,?)',(int(d['material_id']),required(d,'boe'),required(d,'batch'),received,str(qty),str(cost),currency,str(fx))).lastrowid
  c.execute('INSERT INTO movements(batch_id,kind,quantity,reference,created) VALUES(?,?,?,?,?)',(id,'Stock In',str(qty),d['boe'],now));audit(c,user,'Stock received',id,d);return {'id':id}
 if path=='/api/recipes':
  authorize(user,'recipe');pid=int(d['product_id']);one(c,'products',pid);cfg=d['config']
  if cfg.get('basis') not in ['per_piece','batch']:raise RuleError('Select calculation basis')
  if not cfg.get('components') or len(cfg['components'])>12:raise RuleError('Add 1-12 material rules')
  for x in cfg['components']:one(c,'materials',int(x['material_id']))
  requirements(cfg,{'l':60,'w':40,'h':45,'liner_gsm':150,'medium_gsm':120,'gsm':150,'quantity':1})
  version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE product_id=?',(pid,)).fetchone()[0];id=c.execute('INSERT INTO recipes(product_id,version,config,created) VALUES(?,?,?,?)',(pid,version,dumps(cfg),now)).lastrowid;audit(c,user,'Recipe version created',id,{'version':version});return {'id':id}
 if path=='/api/recipes/validate':
  authorize(user,'recipe');r=one(c,'recipes',d['recipe_id']);needs,dims=requirements(json.loads(r['config']),d['inputs']);expected=d['expected']
  for x in needs:
   e=num(expected.get(str(x['material_id'])),'Expected material kg',True);actual=D(x['required'])
   if abs(e-actual)>max(D('.00000001'),actual*D('.000001')):raise RuleError('Sample mismatch',material_id=x['material_id'],actual=str(actual))
  sample={'inputs':d['inputs'],'expected':expected,'actual':needs,'dimensions':dims,'verified_by':user['name'],'date':now};c.execute('UPDATE recipes SET validated=1,sample=? WHERE id=?',(dumps(sample),r['id']));audit(c,user,'Recipe sample validated',r['id'],sample);return {'validated':True}
 if path=='/api/calculate':
  authorize(user,'pi')
  if not isinstance(d.get('prior_calculations',[]),list) or len(d.get('prior_calculations',[]))>50:raise RuleError('Invalid prior PI lines')
  for previous_id in d.get('prior_calculations',[]):
   prior=one(c,'calculations',previous_id)
   if prior['user_id']!=user['id'] and user['role']!='Admin':raise RuleError('Previous calculation belongs to another user',403)
  if d.get('pi_id'):
   original=one(c,'pis',d['pi_id'])
   if original['status'] not in ['Draft','Awaiting LC']:raise RuleError('PI cannot be revised')
  result=calculate(c,d);id=secrets.token_hex(20);c.execute('INSERT INTO calculations VALUES(?,?,?,?,?,?)',(id,d['recipe_id'],dumps(d),dumps(result),time.time(),user['id']));return {'calculation_id':id,**result}
 if path=='/api/sample':
  authorize(user,'recipe');r=one(c,'recipes',d['recipe_id']);needs,dims=requirements(json.loads(r['config']),d);return {'materials':needs,'dimensions':dims}
 if path=='/api/pis':
  authorize(user,'pi');old=one(c,'pis',d['id']) if d.get('id') else None
  if old and old['status'] not in ['Draft','Awaiting LC']:raise RuleError('Linked or cancelled PI cannot be revised')
  if old and int(d.get('expected_revision',0))!=old['revision']:raise RuleError('PI changed. Refresh before revising.',409)
  if old and c.execute("SELECT 1 FROM lcs WHERE pi_id=?",(old['id'],)).fetchone():raise RuleError('An LC draft exists. Resolve it before revising this PI.')
  snap,total=build_pi(c,d,user,old)
  if old:
   id=old['id'];revision=old['revision']+1;release_for_change(c,id,now);c.execute('UPDATE pis SET snapshot=?,total=?,revision=?,status=?,customer_id=?,currency=? WHERE id=?',(dumps(snap),total,revision,'Draft',int(d['customer_id']),d['currency'],id));number=old['number']
  else:
   prefix=setting(c,'company')['prefix'];nextid=c.execute('SELECT COALESCE(MAX(id),0)+1 FROM pis').fetchone()[0];number=f'{prefix}/{snap["date"][:4]}/{nextid:05d}';revision=1;id=c.execute('INSERT INTO pis(number,status,customer_id,snapshot,total,currency,created) VALUES(?,?,?,?,?,?,?)',(number,'Draft',int(d['customer_id']),dumps(snap),total,d['currency'],now)).lastrowid
  c.execute('INSERT INTO pi_versions(pi_id,revision,status,snapshot,total,created) VALUES(?,?,?,?,?,?)',(id,revision,'Draft',dumps(snap),total,now));audit(c,user,'PI revision saved',id,{'number':number,'revision':revision});return {'id':id,'number':number}
 if path in ['/api/pis/approve','/api/pis/cancel']:
  authorize(user,'approve');p=one(c,'pis',d['id'])
  if path.endswith('approve'):
   if p['status']!='Draft':raise RuleError('Only a draft PI can be approved')
   status='Awaiting LC'
  else:
   if p['status'] in ['LC Created','Cancelled']:raise RuleError('Linked or cancelled PI cannot be cancelled')
   status='Cancelled';release_for_change(c,p['id'],now)
  c.execute('UPDATE pis SET status=? WHERE id=?',(status,p['id']));c.execute('UPDATE pi_versions SET status=? WHERE pi_id=? AND revision=?',(status,p['id'],p['revision']));audit(c,user,status,p['id'],{'revision':p['revision']});return {'status':status}
 if path=='/api/reserve':
  authorize(user,'stock');p=one(c,'pis',d['pi_id'])
  if p['status'] not in ['Awaiting LC','LC Created']:raise RuleError('Reserve only against an approved PI')
  if c.execute('SELECT 1 FROM reservations WHERE pi_id=? AND active=1',(p['id'],)).fetchone():raise RuleError('This PI already has active reservations')
  needs={}
  for line in json.loads(p['snapshot'])['lines']:
   for x in line['calculation']['materials']:needs[x['material_id']]=needs.get(x['material_id'],D(0))+D(x['required'])
  details,shortages,_=allocation(c,[{'material_id':k,'required':str(v)} for k,v in needs.items()])
  if shortages:raise RuleError('Reservation rejected; no stock was updated',shortages=shortages)
  for x in details:
   for a in x['allocations']:
    c.execute('INSERT INTO reservations(pi_id,batch_id,quantity) VALUES(?,?,?)',(p['id'],a['batch_id'],a['quantity']));c.execute('INSERT INTO movements(batch_id,kind,quantity,reference,created) VALUES(?,?,?,?,?)',(a['batch_id'],'Reserved',a['quantity'],p['number'],now))
  audit(c,user,'Materials reserved',p['id'],details);return {'reserved':True}
 if path=='/api/reservations/release':
  authorize(user,'stock');active=rows(c,'SELECT * FROM reservations WHERE pi_id=? AND active=1',(d['pi_id'],))
  if not active:raise RuleError('No active reservations')
  for x in active:c.execute('INSERT INTO movements(batch_id,kind,quantity,reference,created) VALUES(?,?,?,?,?)',(x['batch_id'],'Released',x['quantity'],str(d['pi_id']),now))
  c.execute('UPDATE reservations SET active=0 WHERE pi_id=?',(d['pi_id'],));audit(c,user,'Reservations released',d['pi_id'],{});return {'released':True}
 if path=='/api/lcs':
  authorize(user,'finance');p=one(c,'pis',int(d['pi_id']));snap=json.loads(p['snapshot']);status=d.get('status')
  if status not in ['Draft','Confirmed']:raise RuleError('Select draft or confirmed')
  if p['status']!='Awaiting LC':raise RuleError('Select a PI in Awaiting LC')
  amount=num(d['amount'],'LC amount');details={k:str(d.get(k,'')) for k in ['bank','branch','lc_date','forwarding_date','acceptance_date','maturity_date','tenor','bank_reference','up_number','up_date','via_party','comments','beneficiary_account']};details['bank']=required(d,'bank');details['lc_date']=valid_date(d['lc_date'])
  for k in ['forwarding_date','acceptance_date','maturity_date','up_date']:
   if details[k]:details[k]=valid_date(details[k])
  if details['maturity_date'] and details['maturity_date']<details['lc_date']:raise RuleError('Maturity cannot precede LC date')
  details['pi_snapshot']=snap;details['pi_revision']=p['revision'];details['pi_amount']=p['total'];existing=one(c,'lcs',d['id']) if d.get('id') else None
  if existing:
   if existing['status']!='Draft' or existing['pi_id']!=p['id']:raise RuleError('Only a draft LC for the same PI can be updated')
   id=existing['id'];c.execute('UPDATE lcs SET number=?,status=?,amount=?,details=? WHERE id=?',(required(d,'number'),status,str(amount),dumps(details),id))
  else:id=c.execute('INSERT INTO lcs(pi_id,number,status,amount,currency,details,created) VALUES(?,?,?,?,?,?,?)',(p['id'],required(d,'number'),status,str(amount),p['currency'],dumps(details),now)).lastrowid
  if status=='Confirmed':c.execute("UPDATE pis SET status='LC Created' WHERE id=?",(p['id'],));c.execute("UPDATE pi_versions SET status='LC Created' WHERE pi_id=? AND revision=?",(p['id'],p['revision']))
  audit(c,user,'LC '+status,id,{'pi':p['number'],'amount':str(amount),'currency':p['currency']});return {'id':id,'status':status}
 if path=='/api/collections':
  authorize(user,'finance');lc=one(c,'lcs',d['lc_id'])
  if lc['status']!='Confirmed':raise RuleError('Collections require a confirmed LC')
  amount=num(d['amount']);paid=sum((D(x[0]) for x in c.execute('SELECT amount FROM collections WHERE lc_id=?',(lc['id'],))),D(0))
  if paid+amount>D(lc['amount']):raise RuleError('Collection exceeds outstanding amount')
  id=c.execute('INSERT INTO collections(lc_id,amount,date,reference) VALUES(?,?,?,?)',(lc['id'],str(amount),valid_date(d['date']),required(d,'reference'))).lastrowid;audit(c,user,'Collection recorded',id,d);return {'id':id}
 if path=='/api/settings':
  authorize(user,'admin');key=d['key']
  if key not in ['company','template']:raise RuleError('Unknown settings')
  val=d['value']
  if key=='company':
   required(val,'name');prefix=required(val,'prefix')
   if not re.fullmatch(r'[A-Za-z0-9-]{1,20}',prefix):raise RuleError('Prefix must be 1-20 letters, digits or hyphens')
  c.execute('UPDATE settings SET value=? WHERE key=?',(dumps(val),key));audit(c,user,'Settings updated',key,val);return {'saved':True}
 if path=='/api/users':
  authorize(user,'admin');role=d['role']
  if role not in ROLES:raise RuleError('Invalid role')
  if d.get('id'):
   target=one(c,'users',d['id']);active=int(bool(d.get('active',True)))
   if target['id']==user['id'] and (not active or role!='Admin'):raise RuleError('You cannot remove your own admin access')
   c.execute('UPDATE users SET name=?,role=?,active=? WHERE id=?',(required(d,'name'),role,active,target['id']))
   if d.get('password'):c.execute('UPDATE users SET password=? WHERE id=?',(hash_password(d['password']),target['id']))
   c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],));id=target['id']
  else:id=c.execute('INSERT INTO users(name,username,password,role) VALUES(?,?,?,?)',(required(d,'name'),required(d,'username'),hash_password(required(d,'password')),role)).lastrowid
  audit(c,user,'User access updated',id,{'role':role});return {'id':id}
 raise RuleError('Unknown action',404)
if __name__=='__main__':
 from webapp import main
 main()

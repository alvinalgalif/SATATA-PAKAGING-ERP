import unittest,tempfile,json,threading,sqlite3,copy
from pathlib import Path
from decimal import Decimal as D
import server as app
class ERPTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();app.DB=Path(self.tmp.name)/'test.sqlite3';app.initialize();self.user={'id':1,'name':'Demo Test Admin','username':'test-admin','role':'Admin'}
  with app.database(True) as c:c.execute('INSERT INTO users(id,name,username,password,role) VALUES(1,?,?,?,?)',('Demo Test Admin','test-admin',app.hash_password('Test-password-2026'),'Admin'))
 def tearDown(self):self.tmp.cleanup()
 def action(self,path,data,user=None):
  with app.database(True) as c:return app.mutate(c,path,data,user or self.user)
 def read(self,q,args=()):
  with app.database() as c:return app.rows(c,q,args)
 def inputs(self,**kw):return dict(recipe_id=1,l='60',w='40',h='45',liner_gsm='150',medium_gsm='120',gsm='150',quantity='10',currency='BDT',fx='120',markup='20',**kw)
 def validate(self):
  self.action('/api/recipes/validate',{'recipe_id':1,'inputs':{**self.inputs(),'quantity':'1'},'expected':{'1':'.5975856','2':'.334647936','3':'.0181267632'}})
 def stock(self):
  for mid in [1,2,3]:self.action('/api/stock-in',dict(material_id=mid,boe='DEMO-BoE-'+str(mid),batch='DEMO-Lot-A',received='2026-10-01',quantity='1000',unit_cost=str(mid*10),currency='BDT',fx='1'))
 def workflow(self):
  self.validate();self.stock();customer=self.action('/api/customers',{'name':'DEMO customer','address':'DEMO address'})['id'];i=self.inputs();calc=self.action('/api/calculate',i);data={'customer_id':customer,'currency':'BDT','fx':'120','date':'2026-10-24','payment_terms':'DEMO terms only','lines':[{'calculation_id':calc['calculation_id'],'inputs':i}]};pi=self.action('/api/pis',data);return pi,data,calc
 def lc(self,pi_id,status='Confirmed',**kw):return self.action('/api/lcs',{'pi_id':pi_id,'number':'DEMO-LC-001','lc_date':'2026-10-24','bank':'DEMO Bank','amount':'100','status':status,**kw})
 def test_user_confirmed_per_piece_factors_quantity_once(self):
  with app.database() as c:cfg=json.loads(app.one(c,'recipes',1)['config'])
  needs,dims=app.requirements(cfg,self.inputs());self.assertEqual(dims,{'L':'106','W':'87'});self.assertEqual(D(needs[0]['per_piece']),D('.5975856'));self.assertEqual(D(needs[1]['per_piece']),D('.334647936'));self.assertEqual(D(needs[2]['per_piece']),D('.0181267632'));self.assertEqual(D(needs[0]['required']),D('5.975856'))
 def test_recipe_requires_independent_sample(self):
  self.stock();r=self.action('/api/calculate',self.inputs());self.assertFalse(r['ready']);self.assertIsNone(r['unit_price'])
  with self.assertRaises(app.RuleError):self.action('/api/recipes/validate',{'recipe_id':1,'inputs':self.inputs(),'expected':{'1':'1','2':'1','3':'1'}})
 def test_all_material_shortages_returned(self):
  self.validate();r=self.action('/api/calculate',self.inputs());self.assertEqual(len(r['shortages']),3);self.assertFalse(r['ready'])
 def test_fifo_allocation_weighted_cost(self):
  for batch,qty,cost,day in [('A','2','10','01'),('B','8','30','02')]:self.action('/api/stock-in',{'material_id':1,'boe':'DEMO-'+batch,'batch':batch,'received':'2026-10-'+day,'quantity':qty,'unit_cost':cost,'currency':'BDT','fx':'1'})
  with app.database() as c:details,shortages,cost=app.allocation(c,[{'material_id':1,'required':'5'}])
  self.assertFalse(shortages);self.assertEqual(cost,D('110'));self.assertEqual(details[0]['weighted_unit_cost_bdt'],'22');self.assertEqual([x['quantity'] for x in details[0]['allocations']],['2','3'])
 def test_usd_conversion_rate_saved(self):
  self.validate();self.stock();bdt=self.action('/api/calculate',self.inputs());usd=self.action('/api/calculate',{**self.inputs(),'currency':'USD'});self.assertEqual(usd['fx'],'120');self.assertLess(abs(D(bdt['unit_price'])/120-D(usd['unit_price'])),D('.000001'))
 def test_invalid_and_nan_values_rejected(self):
  for val in ['NaN','Infinity','-1','0','bad']:
   with self.assertRaises(app.RuleError):self.action('/api/calculate',{**self.inputs(),'quantity':val})
 def test_calculation_and_pi_save_never_reduce_stock(self):
  pi,data,calc=self.workflow();self.assertTrue(calc['ready']);self.assertEqual([x['quantity'] for x in self.read('SELECT quantity FROM batches')],['1000','1000','1000']);self.assertEqual(len(self.read('SELECT * FROM reservations')),0)
 def test_tampered_line_requires_recalculation(self):
  pi,data,_=self.workflow();bad=copy.deepcopy(data);bad['lines'][0]['inputs']['quantity']='100'
  with self.assertRaises(app.RuleError):self.action('/api/pis',bad)
  self.assertEqual(len(self.read('SELECT * FROM pis')),1)
 def test_pi_approval_draft_lc_confirmation_and_no_deletion(self):
  pi,data,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});lc=self.lc(pi['id'],'Draft');self.assertEqual(self.read('SELECT status FROM pis')[0]['status'],'Awaiting LC');self.lc(pi['id'],id=lc['id']);self.assertEqual(self.read('SELECT status FROM pis')[0]['status'],'LC Created');self.assertEqual(len(self.read('SELECT * FROM pis')),1)
 def test_duplicate_lc_for_same_pi_rolls_back(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});self.lc(pi['id'],'Draft')
  with self.assertRaises(sqlite3.IntegrityError):self.lc(pi['id'],'Draft',number='DEMO-LC-002')
  self.assertEqual(len(self.read('SELECT * FROM lcs')),1);self.assertEqual(self.read('SELECT status FROM pis')[0]['status'],'Awaiting LC')
 def test_partial_collection_outstanding_overpayment_rejected(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});lc=self.lc(pi['id']);self.action('/api/collections',{'lc_id':lc['id'],'amount':'40','date':'2026-10-24','reference':'DEMO payment'})
  with app.database() as c:s=app.state(c,self.user)
  self.assertEqual(D(s['lcs'][0]['outstanding']),D('60'))
  with self.assertRaises(app.RuleError):self.action('/api/collections',{'lc_id':lc['id'],'amount':'61','date':'2026-10-24','reference':'DEMO overpay'})
  self.assertEqual(len(self.read('SELECT * FROM collections')),1)
 def test_reservation_atomic_and_physical_unchanged(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});self.action('/api/reserve',{'pi_id':pi['id']})
  with app.database() as c:b=app.stock(c)
  self.assertEqual(len(self.read('SELECT * FROM reservations WHERE active=1')),3);self.assertEqual(b[0]['on_hand'],'1000');self.assertEqual(D(b[0]['available']),D('994.024144'))
  with self.assertRaises(app.RuleError):self.action('/api/reserve',{'pi_id':pi['id']})
  self.action('/api/reservations/release',{'pi_id':pi['id']})
  with app.database() as c:self.assertEqual(D(app.stock(c)[0]['available']),D('1000'))
 def test_reservation_shortage_rolls_back_all_materials(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']})
  with app.database(True) as c:c.execute("UPDATE batches SET quantity='0.0001' WHERE material_id IN (2,3)")
  with self.assertRaises(app.RuleError) as e:self.action('/api/reserve',{'pi_id':pi['id']})
  self.assertEqual(len(e.exception.details['shortages']),2);self.assertFalse(self.read('SELECT * FROM reservations'));self.assertFalse(self.read("SELECT * FROM movements WHERE kind='Reserved'"))
 def test_recipe_revision_preserves_approved_pi(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});original=self.read('SELECT snapshot FROM pis')[0]['snapshot']
  with app.database() as c:cfg=json.loads(app.one(c,'recipes',1)['config'])
  cfg['components'][0]['wastage']='15';self.action('/api/recipes',{'product_id':1,'config':cfg});self.assertEqual(self.read('SELECT snapshot FROM pis')[0]['snapshot'],original);self.assertEqual(self.read('SELECT validated FROM recipes WHERE version=2')[0]['validated'],0)
 def test_pi_revisions_unique_number_optimistic_lock(self):
  pi,data,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});revision={**data,'id':pi['id'],'expected_revision':1};r=self.action('/api/pis',revision);self.assertEqual(r['number'],pi['number']);self.assertEqual(len(self.read('SELECT * FROM pi_versions')),2);self.assertEqual(self.read('SELECT status FROM pis')[0]['status'],'Draft')
  with self.assertRaises(app.RuleError):self.action('/api/pis',revision)
 def test_permissions_enforced_server_side(self):
  for role in ['Viewer','Sales','Manufacturing','Inventory','Finance','Approver']:
   with self.assertRaises(app.RuleError) as e:self.action('/api/settings',{'key':'template','value':{}},{'id':1,'name':'Demo','role':role})
   self.assertEqual(e.exception.status,403)
  with self.assertRaises(app.RuleError):self.action('/api/stock-in',{}, {'id':1,'name':'Demo','role':'Sales'})
 def test_duplicate_receipt_and_persistence(self):
  self.stock()
  with self.assertRaises(sqlite3.IntegrityError):self.stock()
  self.assertEqual(len(self.read('SELECT * FROM batches')),3);self.assertEqual(len(self.read('SELECT * FROM movements')),3);app.initialize();self.assertEqual(len(self.read('SELECT * FROM batches')),3)
 def test_concurrent_lc_confirmation_only_once(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});out=[]
  def work(n):
   try:self.lc(pi['id'],number='DEMO-'+str(n));out.append('saved')
   except app.RuleError:out.append('rejected')
  ts=[threading.Thread(target=work,args=(n,)) for n in range(2)]
  for t in ts:t.start()
  for t in ts:t.join()
  self.assertEqual(sorted(out),['rejected','saved']);self.assertEqual(len(self.read('SELECT * FROM lcs')),1)
 def test_dimension_factors_and_negative_allowance(self):
  with app.database() as c:cfg=json.loads(app.one(c,'recipes',1)['config'])
  cfg['dimensions']={'L':{'factors':{'l':'1','w':'0','h':'2'},'allowance':'-2'},'W':{'factors':{'l':'0','w':'1','h':'0'},'allowance':'-2'}}
  needs,dims=app.requirements(cfg,self.inputs());self.assertEqual(dims,{'L':'148','W':'38'})
 def test_multi_line_fifo_costs_remaining_batches(self):
  self.validate();self.stock();customer=self.action('/api/customers',{'name':'DEMO Multi','address':'DEMO'})['id']
  with app.database(True) as c:c.execute("UPDATE batches SET quantity='8' WHERE material_id=1")
  self.action('/api/stock-in',dict(material_id=1,boe='DEMO-Next',batch='B',received='2026-10-02',quantity='100',unit_cost='50',currency='BDT',fx='1'))
  first=self.inputs();a=self.action('/api/calculate',first);second={**first,'prior_calculations':[a['calculation_id']]};b=self.action('/api/calculate',second)
  self.assertGreater(D(b['unit_price']),D(a['unit_price']));self.assertEqual(len(b['materials'][0]['allocations']),2)
  p=self.action('/api/pis',{'customer_id':customer,'currency':'BDT','fx':'120','date':'2026-10-24','payment_terms':'DEMO','lines':[{'calculation_id':a['calculation_id'],'inputs':first},{'calculation_id':b['calculation_id'],'inputs':second}]});self.assertTrue(p['id'])
 def test_two_paper_recipe_accepts_blank_or_omitted_general_gsm(self):
  self.validate();self.stock()
  baseline=self.action('/api/calculate',self.inputs())
  for blank in ['',None,'   ']:
   inputs={**self.inputs(),'gsm':blank};result=self.action('/api/calculate',inputs)
   self.assertTrue(result['ready']);self.assertEqual(result['unit_price'],baseline['unit_price'])
  inputs=self.inputs();del inputs['gsm'];self.assertTrue(self.action('/api/calculate',inputs)['ready'])
 def single_gsm_fixture(self):
  # Synthetic isolated test configuration, never an enabled production product formula.
  with app.database() as c:cfg=json.loads(app.one(c,'recipes',1)['config'])
  cfg['components']=cfg['components'][:1];cfg['components'][0]['gsm']='gsm'
  rid=self.action('/api/recipes',{'product_id':1,'config':cfg})['id']
  inputs={**self.inputs(),'recipe_id':rid,'liner_gsm':'','medium_gsm':'','quantity':'1'}
  self.action('/api/recipes/validate',{'recipe_id':rid,'inputs':inputs,'expected':{'1':'.5975856'}})
  return inputs
 def test_single_gsm_recipe_calculates_and_saves_pi_with_other_gsms_blank(self):
  inputs=self.single_gsm_fixture();self.stock();inputs['quantity']='10';result=self.action('/api/calculate',inputs);self.assertTrue(result['ready'])
  customer=self.action('/api/customers',{'name':'DEMO single GSM','address':'DEMO'})['id']
  saved=self.action('/api/pis',{'customer_id':customer,'currency':'BDT','fx':'120','date':'2026-10-24','payment_terms':'DEMO','lines':[{'calculation_id':result['calculation_id'],'inputs':inputs}]})
  snapshot=json.loads(self.read('SELECT snapshot FROM pis WHERE id=?',(saved['id'],))[0]['snapshot']);self.assertEqual(snapshot['lines'][0]['inputs']['liner_gsm'],'');self.assertEqual(snapshot['lines'][0]['inputs']['medium_gsm'],'')
 def test_required_gsm_never_silently_becomes_zero(self):
  for key,label in [('liner_gsm','Liner paper GSM'),('medium_gsm','Medium paper GSM')]:
   for bad in ['',None,'0','NaN','bad','-1']:
    with self.assertRaises(app.RuleError) as e:self.action('/api/calculate',{**self.inputs(),key:bad,'gsm':''})
    self.assertIn(label,e.exception.message)
  inputs=self.single_gsm_fixture()
  for bad in ['',None,'0','NaN','bad','-1']:
   with self.assertRaises(app.RuleError) as e:self.action('/api/calculate',{**inputs,'gsm':bad})
   self.assertIn('GSM (required by recipe)',e.exception.message)
 def test_sample_validation_accepts_unused_gsm_blank(self):
  inputs={**self.inputs(),'quantity':'1','gsm':''}
  self.action('/api/recipes/validate',{'recipe_id':1,'inputs':inputs,'expected':{'1':'.5975856','2':'.334647936','3':'.0181267632'}})
  self.assertEqual(self.read('SELECT validated FROM recipes WHERE id=1')[0]['validated'],1)
 def test_product_add_and_edit_counting_units(self):
  for unit in ['PCS','DZN','ROLL','KG']:
   created=self.action('/api/products',{'name':'DEMO '+unit,'unit':unit,'hs_code':'4819'})
   saved=self.read('SELECT * FROM products WHERE id=?',(created['id'],))[0];self.assertEqual(saved['unit'],unit)
   self.action('/api/products',{'id':created['id'],'name':'DEMO renamed '+unit,'unit':'kg','hs_code':'4820'})
   saved=self.read('SELECT * FROM products WHERE id=?',(created['id'],))[0];self.assertEqual(saved['name'],'DEMO renamed '+unit);self.assertEqual(saved['unit'],'KG');self.assertEqual(saved['hs_code'],'4820')
  self.assertEqual(len(self.read("SELECT * FROM audit WHERE action='Product updated'")),4)
 def test_product_invalid_unit_and_duplicate_name_roll_back(self):
  original=self.read('SELECT * FROM products WHERE id=1')[0]
  for unit in ['BOX','',None]:
   with self.assertRaises(app.RuleError):self.action('/api/products',{'id':1,'name':'DEMO changed','unit':unit})
   self.assertEqual(self.read('SELECT * FROM products WHERE id=1')[0],original)
  with self.assertRaises(sqlite3.IntegrityError):self.action('/api/products',{'id':1,'name':'MASTER CARTOON 5 PLY','unit':'ROLL'})
  self.assertEqual(self.read('SELECT * FROM products WHERE id=1')[0],original)
 def test_product_edit_preserves_recipe_links_and_saved_pi_snapshot(self):
  pi,_,_=self.workflow();self.action('/api/pis/approve',{'id':pi['id']});before=self.read('SELECT snapshot FROM pis WHERE id=?',(pi['id'],))[0]['snapshot']
  self.action('/api/products',{'id':1,'name':'DEMO renamed carton','unit':'DZN','hs_code':'4819'})
  self.assertEqual(self.read('SELECT product_id FROM recipes WHERE id=1')[0]['product_id'],1);self.assertEqual(self.read('SELECT snapshot FROM pis WHERE id=?',(pi['id'],))[0]['snapshot'],before)
  detail=json.loads(self.read("SELECT detail FROM audit WHERE action='Product updated'")[0]['detail']);self.assertEqual(detail['before']['unit'],'PCS');self.assertEqual(detail['after']['unit'],'DZN')
 def test_product_edit_enforces_permissions_and_missing_record(self):
  with self.assertRaises(app.RuleError) as e:self.action('/api/products',{'id':1,'name':'DEMO','unit':'KG'},{'id':1,'name':'DEMO Sales','role':'Sales'})
  self.assertEqual(e.exception.status,403)
  with self.assertRaises(app.RuleError) as e:self.action('/api/products',{'id':999999,'name':'DEMO','unit':'KG'})
  self.assertEqual(e.exception.status,404)
if __name__=='__main__':unittest.main(verbosity=2)

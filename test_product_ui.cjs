const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({document:{addEventListener(){}},fetch:()=>new Promise(()=>{}),console});vm.runInContext(fs.readFileSync('dist/app.js','utf8'),context);
vm.runInContext(`S={permissions:['recipe'],products:[{id:1,name:'DEMO <Carton>',hs_code:'4819',unit:'DZN'}],recipes:[{id:1,product_id:1,version:1,validated:1}]};modal=(...args)=>{globalThis.modalArgs=args;}`,context);
vm.runInContext('productForm()',context);assert.equal(context.modalArgs[0],'Add finished product');let html=context.modalArgs[1];for(const unit of ['PCS','DZN','ROLL','KG'])assert.ok(html.includes('value="'+unit+'"'));assert.match(html,/value="PCS" selected/);
vm.runInContext('productForm(S.products[0])',context);assert.equal(context.modalArgs[0],'Edit finished product');html=context.modalArgs[1];assert.match(html,/value="DZN" selected/);assert.match(html,/name="id" value="1"/);assert.match(html,/DEMO &lt;Carton&gt;/);assert.match(html,/value="4819"/);
html=vm.runInContext('manufacturing()',context);assert.match(html,/data-action="product-edit"/);assert.match(html,/data-action="recipe-edit"/);assert.match(html,/>DZN<\/td>/);
vm.runInContext('S.permissions=[]',context);html=vm.runInContext('manufacturing()',context);assert.doesNotMatch(html,/data-action="product-edit"/);
console.log('Product create/edit form, four counting units, catalog actions and role visibility passed.');

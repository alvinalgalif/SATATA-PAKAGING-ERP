const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({document:{addEventListener(){}},fetch:()=>new Promise(()=>{}),console});
vm.runInContext(fs.readFileSync('dist/app.js','utf8'),context);
vm.runInContext(`S={recipes:[{id:1,product_id:1,version:1,validated:1,config:{components:[{kind:'paper',gsm:'liner_gsm'},{kind:'paper',gsm:'medium_gsm'}]}},{id:2,product_id:2,version:1,validated:1,config:{components:[{kind:'paper',gsm:'gsm'}]}}],products:[{id:1,name:'DEMO two-paper'},{id:2,name:'DEMO single-GSM'}]}`,context);
let checks=0;
function input(html,name){let found=html.match(new RegExp('<input[^>]*name="'+name+'"[^>]*>'));assert.ok(found);checks++;return found[0]}
let two=vm.runInContext(`calcFields({recipe_id:1})`,context);
assert.match(input(two,'liner_gsm'),/\brequired\b/);assert.match(input(two,'medium_gsm'),/\brequired\b/);assert.doesNotMatch(input(two,'gsm'),/\brequired\b/);assert.match(input(two,'gsm'),/value=""/);
let single=vm.runInContext(`calcFields({recipe_id:2,gsm:'150',liner_gsm:'',medium_gsm:''})`,context);
assert.match(input(single,'gsm'),/\brequired\b/);assert.doesNotMatch(input(single,'liner_gsm'),/\brequired\b/);assert.doesNotMatch(input(single,'medium_gsm'),/\brequired\b/);
const elements=Object.fromEntries(['liner_gsm','medium_gsm','gsm'].map(name=>[name,{required:false}]));const hints=Object.fromEntries(Object.keys(elements).map(name=>[name,{textContent:''}]));context.form={querySelector(selector){let m=selector.match(/\[name="(.*?)"\]/);return m?elements[m[1]]:hints[selector.match(/\[data-gsm-hint="(.*?)"\]/)[1]]}};
vm.runInContext(`updateGsmRequirements(form,S.recipes[0].config)`,context);assert.equal(elements.liner_gsm.required,true);assert.equal(elements.medium_gsm.required,true);assert.equal(elements.gsm.required,false);
vm.runInContext(`updateGsmRequirements(form,S.recipes[1].config)`,context);assert.equal(elements.liner_gsm.required,false);assert.equal(elements.medium_gsm.required,false);assert.equal(elements.gsm.required,true);assert.match(hints.gsm.textContent,/Required/);checks+=7;
vm.runInContext(`recipeDraft={id:2}`,context);let sample=vm.runInContext(`calcFields({...sampleDefaults,liner_gsm:'',medium_gsm:''},false)`,context);assert.match(input(sample,'gsm'),/\brequired\b/);assert.doesNotMatch(input(sample,'liner_gsm'),/\brequired\b/);
console.log('GSM form rendering, sample fields and recipe-change validation passed ('+checks+' field checks).');

const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({document:{addEventListener(){}},fetch:()=>new Promise(()=>{}),console});vm.runInContext(fs.readFileSync('dist/app.js','utf8'),context);
function match(text,query){return vm.runInContext('matchesSearch('+JSON.stringify(text)+','+JSON.stringify(query)+')',context)}
assert.equal(match('MASTER CARTOON 3 PLY PCS','master 3 ply'),true);
assert.equal(match('MASTER CARTOON 5 PLY PCS','master 3 ply'),false);
assert.equal(match('DEMO customer','MASTER CARTOON'),false);
assert.equal(match('পণ্য নীল বাক্স','নীল বাক্স'),true);
assert.equal(match('PI/2026/001 Awaiting LC','001 awaiting'),true);
assert.equal(match('A\nB\tC',' a  c '),true);
assert.equal(match('ＰＣＳ fullwidth','pcs'),true);
assert.equal(match('anything','  '),true);
assert.equal(match('Product name','<script>alert(1)</script>'),false);
context.mockTable={querySelectorAll(){return [{hidden:false,dataset:{searchIndex:'0'}},{hidden:true,dataset:{searchIndex:'1'}},{hidden:false,dataset:{searchIndex:'2'}}]}};
context.document.querySelector=()=>context.mockTable;
vm.runInContext('reportRows=[["visible first"],["hidden other"],["visible third"]]',context);
assert.equal(JSON.stringify(vm.runInContext('visibleReportRows()',context)),JSON.stringify([['visible first'],['visible third']]));
context.mockTable.querySelectorAll=()=>[{hidden:true,dataset:{searchIndex:'0'}}];assert.equal(vm.runInContext('visibleReportRows().length',context),0);
vm.runInContext('scopedSearchQueries.set("catalog","paper");scopedSearchQueries.set("customers","buyer");scopedSearchQueries.delete("catalog")',context);assert.equal(vm.runInContext('scopedSearchQueries.get("customers")',context),'buyer');
console.log('Search matching, Bangla/English normalization, multi-word results, no matches, query separation and filtered report export passed.');

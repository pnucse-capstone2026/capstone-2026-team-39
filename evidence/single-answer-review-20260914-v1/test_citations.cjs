'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const startup = source.lastIndexOf('\n(async()=>{');
assert.ok(startup > 0, 'app startup boundary must exist');

function citation(overrides = {}) {
  return {source_number:1, source_title:'합성 출처', claim_text:'합성 답변 문장', excerpt:'UNIQUE_QUOTE_QA', page:null, section_path:null, ...overrides};
}
function render(citations, actualItem) {
  const item = actualItem || {item_id:'BTEST', phase:'main', question:'합성 질문', role:'합성 역할', answerable:true,
    answer:'합성 최종 답변', required:[], oracle:{must_do:[],must_not_do:[]}, forbidden_claims:[],
    contexts:[], contexts_exact_prompt_blocks:true, citations, applicable:{correct_abstention:false,injection_obedience:false}};
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML:'',style:{},classList:{toggle(){}},querySelectorAll(){return [];},scrollIntoView(){}});
    return nodes.get(id);
  };
  const context = vm.createContext({URLSearchParams,location:{hash:'#token=synthetic'},
    document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){}},
    window:{addEventListener(){}}, fixture:JSON.parse(JSON.stringify(item))});
  vm.runInContext(source.slice(0,startup), context);
  const before = JSON.stringify(context.fixture);
  vm.runInContext(`packet={items:[fixture]}; position=0; data={reviewer_id:'SYNTHETIC',labels:[{score:null,grounded_fully_correct:null,uncertain:false,uncertain_reason:'',notes:'',individual_judge_exposed:false,confirmed_at:null}]}; render();`,context);
  assert.equal(JSON.stringify(context.fixture), before, 'display must not mutate the original artifact');
  return element('main').innerHTML;
}
test('12 identical location rows display the quote once', () => {
  const html=render(Array.from({length:12},()=>citation()));
  assert.equal(html.split('UNIQUE_QUOTE_QA').length-1,1);
  assert.match(html,/원본 12행/);
});
test('different claims sharing one excerpt are kept distinct', () => {
  const html=render([citation(),citation({claim_text:'다른 주장'})]);
  assert.equal(html.split('UNIQUE_QUOTE_QA').length-1,2);
});
test('different source numbers or titles are not merged', () => {
  const html=render([citation(),citation({source_number:2}),citation({source_title:'다른 출처'})]);
  assert.equal(html.split('UNIQUE_QUOTE_QA').length-1,3);
});
test('different excerpts are preserved verbatim', () => {
  const html=render([citation(),citation({excerpt:'ANOTHER_QUOTE_QA'})]);
  assert.ok(html.includes('ANOTHER_QUOTE_QA'));assert.ok(html.includes('UNIQUE_QUOTE_QA'));
});
test('page and section variants remain visible inside the group', () => {
  const html=render([citation({page:3,section_path:['표 A']}),citation({page:8,section_path:['표 B']})]);
  assert.equal(html.split('UNIQUE_QUOTE_QA').length-1,1);
  assert.match(html,/3쪽/);assert.match(html,/8쪽/);assert.ok(html.includes('표 A'));assert.ok(html.includes('표 B'));
});
test('untrusted source and quote text is escaped', () => {
  const html=render([citation({source_title:'<img src=x onerror=alert(1)>',excerpt:'<script>attack()</script>'})]);
  assert.ok(!html.includes('<script>attack'));assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('&lt;script&gt;attack()&lt;/script&gt;'));
});
test('empty citations remain empty', () => {
  const html=render([]);assert.match(html,/답변에 연결된 인용 0개/);
  assert.equal((html.match(/class="gold citation-group"/g)||[]).length,0);
});
test('real P01 and B002 reduce to 4 and 7 groups without changing packets', () => {
  const packetPath=path.join(__dirname,'../../processed/reviews/single-answer-20260914-v1/packet.json');
  const packet=JSON.parse(fs.readFileSync(packetPath,'utf8'));
  for(const [id,expected,raw] of [['P01',4,46],['B002',7,390]]) {
    const item=packet.items.find(i=>i.item_id===id);assert.equal(item.citations.length,raw);
    const html=render(null,item);
    assert.ok(html.includes(`답변에 연결된 인용 ${expected}개`),`${id}: grouped citation count must be ${expected}`);
    assert.equal((html.match(/class="gold citation-group"/g)||[]).length,expected);
  }
});

// Exercise real render handlers and flush(), with only the network replaced.
// These synthetic labels exist in an isolated VM and never reach a server.
function gradeFixture() {
  const nodes=new Map(), storage=new Map();
  const element=id=>{
    if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',style:{},classList:{toggle(){}},querySelectorAll(){return [];},scrollIntoView(){}});
    return nodes.get(id);
  };
  const inputs=[0,1,2].map(v=>({dataset:{key:'score'},value:String(v),checked:false}));
  inputs.push(...[false,true].map(v=>({dataset:{key:'grounded_fully_correct'},value:String(v),checked:false})));
  const c=vm.createContext({URLSearchParams,TextEncoder,crypto:require('node:crypto').webcrypto,clearTimeout,
    location:{hash:'#token=synthetic'},localStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k);}},
    document:{getElementById:element,querySelectorAll(selector){return selector==='input[data-key]'?inputs:inputs.filter(e=>e.dataset.key==='grounded_fully_correct');},addEventListener(){}},
    window:{addEventListener(){}}});
  vm.runInContext(source.slice(0,startup),c);
  vm.runInContext(`packet={items:[{item_id:'SYNTHETIC',phase:'main',question:'synthetic',role:'synthetic',answerable:true,answer:'synthetic',required:[],oracle:{must_do:[],must_not_do:[]},forbidden_claims:[],contexts:[],contexts_exact_prompt_blocks:true,citations:[],applicable:{correct_abstention:false,injection_obedience:false}}]};
    pin='synthetic-pin';draftKey='synthetic-only';
    data={reviewer_id:'SYNTHETIC',packet_sha256:pin,labels:[{score:null,grounded_fully_correct:null,uncertain:false,uncertain_reason:'',notes:'',individual_judge_exposed:false,confirmed_at:null}]};
    receipt={revision:0,data:clone(data)};
    api=async(path,sent)=>({revision:receipt.revision+1,data:clone(sent.data),sha256:await hash(sent.data),saved_at:'2026-09-14T00:00:00Z'});
    changed=()=>{};render();`,c);
  return {
    choose(key,value){const input=inputs.find(e=>e.dataset.key===key&&e.value===String(value));inputs.filter(e=>e.dataset.key===key).forEach(e=>e.checked=false);input.checked=true;input.onchange();return input.checked;},
    save(){return vm.runInContext('flush()',c);},
    state(){return JSON.parse(vm.runInContext('JSON.stringify(data.labels[0])',c));},
    status(){return element('status').textContent;}
  };
}
for(const previous of [0,1])test(`after saving ${previous}, changing to 2 permits GFC pass`,async()=>{
  const f=gradeFixture();f.choose('score',previous);await f.save();
  f.choose('score',2);assert.equal(f.choose('grounded_fully_correct',true),true);
  assert.equal(f.state().grounded_fully_correct,true);await f.save();assert.equal(f.state().score,2);
});
for(const next of [0,1])test(`after saving 2, changing to ${next} still forbids GFC pass`,async()=>{
  const f=gradeFixture();f.choose('score',2);f.choose('grounded_fully_correct',true);await f.save();
  f.choose('score',next);assert.equal(f.state().grounded_fully_correct,null);
  assert.equal(f.choose('grounded_fully_correct',true),false);assert.equal(f.state().grounded_fully_correct,null);
  assert.match(f.status(),/2점일 때만/);
});
test('unchanged score 2 permits GFC after saving',async()=>{
  const f=gradeFixture();f.choose('score',2);await f.save();
  assert.equal(f.choose('grounded_fully_correct',true),true);assert.equal(f.state().grounded_fully_correct,true);
});
test('changing score before first save also permits GFC',()=>{
  const f=gradeFixture();f.choose('score',1);f.choose('score',2);
  assert.equal(f.choose('grounded_fully_correct',true),true);
});

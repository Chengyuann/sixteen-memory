import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile, writeFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const cf=resolve(import.meta.dirname,'..');
const out=resolve(process.env.OUTPUT ?? '../../../outputs/triple-smoke-20261029/worker');
const input=process.env.DATA;
const split=process.env.SPLIT ?? 'validation';
const selected=(process.env.VARIANTS ?? 'BASE,T03,T04,T05').split(',');
const hash=x=>createHash('sha256').update(x).digest('hex');
const config=JSON.parse(await readFile(join(cf,'wrangler.jsonc'),'utf8'));
await mkdir(out,{recursive:true});
const bytes=await readFile(input);const dataset=JSON.parse(bytes);
const conversations=dataset.conversations.filter(c=>c.split===split);
assert.ok(conversations.length);
const mean=a=>a.length?a.reduce((s,x)=>s+x,0)/a.length:null;
const macro=(rows,field)=>{
 const strata=new Map();
 for(const r of rows.filter(r=>r.answerable)){const k=JSON.stringify([r.dataset,r.category]);if(!strata.has(k))strata.set(k,[]);strata.get(k).push(r[field]);}
 return mean([...strata.values()].map(mean));
};
function coverage(intervals,length){let end=0,n=0;for(const[a,b]of intervals.sort((a,b)=>a[0]-b[0])){n+=Math.max(0,Math.min(length,b)-Math.max(end,a));end=Math.max(end,b);}return n/length;}
for(const variant of selected){
 assert.ok(['BASE','T03','T04','T05'].includes(variant));
 const resultPath=join(out,`${split}-${variant}.json`);const bundle=join(out,`${variant}.js`);
 const baselineDir=join(cf,'candidates','BASE');
 if(variant==='BASE'){
  await mkdir(baselineDir,{recursive:true});
  for(const file of ['index.ts','core.ts','models.ts'])await writeFile(join(baselineDir,file),execFileSync('git',['show',`a3520ccb7843156e2dd51e92e29caf0f6dafafa4:cloudflare/src/${file}`],{cwd:cf}));
 }
 const sourceDir=join(cf,'candidates',variant);
 const sourceParts=[];for(const f of ['index.ts','core.ts','models.ts'])sourceParts.push(await readFile(join(sourceDir,f)));if(variant!=='BASE'){sourceParts.push(await readFile(join(cf,'candidates/rerank.ts')));sourceParts.push(await readFile(join(cf,'candidates/BASE/core.ts')));}
 const source_sha256=hash(Buffer.concat(sourceParts));
 await build({entryPoints:[join(sourceDir,'index.ts')],outfile:bundle,bundle:true,format:'esm',platform:'neutral',target:'es2022',conditions:['workerd','worker','browser'],external:['cloudflare:workers']});
 const directory=await mkdtemp(join(tmpdir(),'sixteen-three-'));
 const key='only-synthetic-offline-runtime-test-key';
 const mf=new Miniflare({...convertV4MiniflareOptions({name:'research-'+variant,modules:true,modulesRoot:out,scriptPath:bundle,compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,bindings:{...config.vars,MEMORY_API_KEY:key,SUBMISSION_ID:'frozen-'+variant,SOURCE_COMMIT:'local-audit'},durableObjects:{MEMORY:{className:'UserMemory',useSQLite:true}}}),resourcePersistencePath:directory});
 const rows=[];
 const post=async(route,payload)=>{const r=await mf.dispatchFetch('http://localhost/v1/memories/'+route,{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify(payload)});assert.equal(r.status,200,`${variant} ${route}: ${r.status}`);return r.json();};
 try{
  for(const c of conversations){
   const user='source-'+hash(c.id).slice(0,16);const sources=new Map();
   for(const b of c.batches){
    const messages=b.messages.map((m,i)=>{
     const timestamp=m.timestamp??b.timestamp??null;
     sources.set(`${b.request_id}:${i}`,{text:m.content,chars:Array.from(m.content),timestamp,role:m.role,session:b.session_id});
     return {role:m.role,content:m.content,timestamp};
    });
    await post('add',{user_id:user,request_id:b.request_id,session_id:b.session_id,messages});
   }
   for(const q of c.queries){
    const start=performance.now();const result=await post('search',{user_id:user,query:q.query,top_k:100,...(q.options?{options:q.options}:{})});
    const ms=performance.now()-start;
    assert.ok(result.data.length<=100);const identities=[];const spans=[];const signatures=[];let chars=0;let last=Infinity;
    for(const d of result.data){
     assert.ok(Number.isFinite(d.score));assert.ok(d.score<=last+1e-12);last=d.score;
     const n=d.content.indexOf('\n');assert.ok(n>0);const meta=JSON.parse(d.content.slice(0,n));const text=d.content.slice(n+1);const id=`${meta.request_id}:${meta.message}`;const source=sources.get(id);assert.ok(source,'unknown source');
     const length=Array.from(text).length;assert.ok(length>0);assert.equal(source.chars.slice(meta.character_offset,meta.character_offset+length).join(''),text);assert.equal(meta.session,source.session);assert.equal(meta.role,source.role);assert.equal(meta.source_time,source.timestamp===null?null:new Date(source.timestamp).toISOString());
     identities.push(id);chars+=length;spans.push({id,start:meta.character_offset,end:meta.character_offset+length,chars:length});signatures.push(JSON.stringify([id,meta.character_offset,text]));
    }
    assert.equal(new Set(signatures).size,signatures.length);
    const required=new Set((q.required??[]).map(r=>`${r.request_id}:${r.position}`));const forbidden=new Set((q.forbidden??[]).map(r=>`${r.request_id}:${r.position}`));
    const metrics={};for(const k of [10,20,100])metrics['recall'+k]=required.size?[...required].filter(x=>identities.slice(0,k).includes(x)).length/required.size:null;
    const covers=[...required].map(id=>coverage(spans.filter(x=>x.id===id).map(x=>[x.start,x.end]),sources.get(id).chars.length));
    let budget=0;const limited=[];for(const s of spans){if(budget+s.chars>16000)break;budget+=s.chars;limited.push(s.id);}
    rows.push({id:q.id,dataset:c.dataset,category:q.category,split,answerable:required.size>0,...metrics,source_coverage100:mean(covers),recall16k:required.size?[...required].filter(x=>limited.includes(x)).length/required.size:null,forbidden_hits:[...forbidden].filter(x=>identities.includes(x)).length,characters:chars,ms,evidence_set_sha256:hash(JSON.stringify(signatures.slice().sort())),evidence_order_sha256:hash(JSON.stringify(signatures)),returned:identities.length});
   }
   console.log(JSON.stringify({variant,split,complete:rows.length,conversation:c.id}));
  }
  const summary={};for(const f of ['recall10','recall20','recall100','source_coverage100','recall16k'])summary[f]=macro(rows,f);
  summary.evidence_score=100*summary.recall20;summary.forbidden_hits=rows.reduce((n,r)=>n+r.forbidden_hits,0);summary.p95_ms=rows.map(r=>r.ms).sort((a,b)=>a-b)[Math.ceil(rows.length*.95)-1];
  const result={variant,source_sha256,split,questions:rows.length,data_sha256:hash(bytes),bundle_sha256:hash(await readFile(bundle)),summary,rows,errors:0,model_calls:0,official_evaluations:0,baseline:'a3520ccb7843156e2dd51e92e29caf0f6dafafa4'};
  await writeFile(resultPath,JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({variant,split,summary}));
 }finally{await mf.dispose();await rm(directory,{recursive:true,force:true});}
}

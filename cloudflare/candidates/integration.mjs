import assert from "node:assert/strict";
import { readFile, writeFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { runAuthenticatedSuite } from "../scripts/authenticated-suite.mjs";

const cf=resolve(import.meta.dirname,'..');
const out=resolve(process.env.OUTPUT ?? '../../../outputs/triple-smoke-20261029/integration');
await mkdir(out,{recursive:true});
const checksum=x=>createHash('sha256').update(x).digest('hex');
const versions={T03:'0.2.7',T04:'0.2.8',T05:'0.2.9'};
for(const id of Object.keys(versions)){
 const config=JSON.parse(await readFile(join(cf,'candidates',id,'wrangler.jsonc'),'utf8'));
 const bundle=join(out,id+'.js');
 await build({entryPoints:[join(cf,'candidates',id,'index.ts')],outfile:bundle,bundle:true,format:'esm',target:'es2022',platform:'neutral',conditions:['workerd','worker','browser'],external:['cloudflare:workers']});
 const pieces=[];for(const file of ['index.ts','core.ts','models.ts'])pieces.push(await readFile(join(cf,'candidates',id,file)));
 pieces.push(await readFile(join(cf,'candidates/rerank.ts')),await readFile(join(cf,'candidates/BASE/core.ts')));
 const directory=await mkdtemp(join(tmpdir(),'three-integration-'));
 const key='synthetic-offline-only-authentication-key';
 const mf=new Miniflare({...convertV4MiniflareOptions({name:config.name,modules:true,modulesRoot:out,scriptPath:bundle,compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,bindings:{...config.vars,MEMORY_API_KEY:key,SOURCE_COMMIT:'a'.repeat(40)},durableObjects:{MEMORY:{className:'UserMemory',useSQLite:true}}}),resourcePersistencePath:directory});
 try{
  const report=await runAuthenticatedSuite({send:(url,init)=>mf.dispatchFetch(url.toString(),init),baseUrl:'https://synthetic.invalid',key,loadRecords:1000,loadQueries:20});
  assert.equal(report.status,'passed',JSON.stringify(report.failure));assert.equal(report.version,versions[id]);
  const requests=[];const post=async(route,data)=>{
   const response=await mf.dispatchFetch('https://synthetic.invalid/v1/memories/'+route,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+key},body:JSON.stringify(data)});
   requests.push({route,status:response.status});return {status:response.status,body:await response.json()};
  };
  const payload=(scope,i)=>({user_id:'synthetic16-'+scope,request_id:'w'+i,session_id:'s'+i,messages:[{role:'user',content:`Parallel marker${i} value${i}.`}]});
  const distinct=await Promise.all(Array.from({length:16},(_,i)=>post('add',payload('d'+i,i))));assert.ok(distinct.every(r=>r.status===200));
  const found=await Promise.all(Array.from({length:16},(_,i)=>post('search',{user_id:'synthetic16-d'+i,query:'Parallel',top_k:100})));
  found.forEach((r,i)=>{assert.equal(r.status,200);assert.equal(r.body.data.length,1);assert.ok(r.body.data[0].content.endsWith(`Parallel marker${i} value${i}.`));});
  const shared=await Promise.all(Array.from({length:16},(_,i)=>post('add',payload('shared',i))));assert.ok(shared.every(r=>[200,429].includes(r.status)));
  for(let i=0;i<16;i++)if(shared[i].status===429)assert.equal((await post('add',payload('shared',i))).status,200);
  const all=await post('search',{user_id:'synthetic16-shared',query:'Parallel',top_k:100});assert.equal(all.body.data.length,16);
  const empty=await post('search',{user_id:'synthetic16-unwritten',query:'Summary rules Atlas',top_k:100});assert.deepEqual(empty.body,{data:[]});
  const result={id,status:'passed',source_sha256:checksum(Buffer.concat(pieces)),bundle_sha256:checksum(await readFile(bundle)),authenticated:report,concurrency16:{status:'passed',requests:requests.length,statuses:requests.reduce((o,r)=>(o[r.status]=(o[r.status]??0)+1,o),{})},model_calls:0,official_evaluations:0};
  await writeFile(join(out,id+'.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({id,status:'passed',groups:report.checks.length,concurrency16:result.concurrency16}));
 }finally{await mf.dispose();await rm(directory,{recursive:true,force:true});}
}

import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL?.startsWith('file:') && !context.parentURL.includes('/node_modules/')) {
      const base = fileURLToPath(new URL(specifier, context.parentURL));
      for (const candidate of [base, base + '.ts', base + '.tsx', base + '/index.ts', base + '/index.tsx']) {
        if (existsSync(candidate) && statSync(candidate).isFile()) return nextResolve(pathToFileURL(candidate).href, context);
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.ts') || url.endsWith('.tsx')) return {
      format: 'module', shortCircuit: true,
      source: stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {mode:'strip'})
    };
    return nextLoad(url, context);
  }
});
const {createAppStore} = await import('./store.ts');
const {analysisActions,phenotypeActions,workspaceActions,chatActions,historyActions} = await import('./slices/index.ts');
const {caseApi,ApiError} = await import('../shared/api/index.ts');
const {parseSse} = await import('../shared/api/sse.ts');
const adapters = await import('../shared/api/adapters.ts');
const F = await import('../mocks/backendFixtures.ts');
const {sampleCases} = await import('../mocks/index.ts');
const {snapshotFromView} = await import('../shared/api/caseFromView.ts');
const {selectCanRetrieve,selectHasAnalyzed,selectBusy,selectCanEdit,selectCanChat,selectSelectedEvidence} = await import('./selectors/index.ts');
const {applyStep,endTurn} = await import('./slices/turnItems.ts');
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function until(check, tries=140) { for(let i=0;i<tries;i++){if(check())return;await pause(20);} throw new Error('State transition timed out'); }
let checks=0;
function check(label, fn){fn();checks++;console.log('PASS '+label);}

/* Fake backend: each caseApi method records its call and answers from a queue, else from a default. */
const calls=[];
const queues={};
const aborted=[];
const respond=(method,...answers)=>{(queues[method]??=[]).push(...answers);};
const callsOf=method=>calls.filter(c=>c.method===method);
async function* fakeStream(answer, signal){
  if(answer instanceof Error) throw answer;
  const {events,delayMs=1,hang=false}=Array.isArray(answer)?{events:answer}:answer;
  for(const event of events){await pause(delayMs);if(signal?.aborted)throw new DOMException('aborted','AbortError');yield event;}
  if(hang) await new Promise((_,reject)=>{const stop=()=>{aborted.push(true);reject(new DOMException('aborted','AbortError'));};if(signal?.aborted)stop();else signal?.addEventListener('abort',stop);});
}
async function fakeJson(answer, signal){
  if(typeof answer==='function') answer=answer();
  const {value,delayMs=1}=answer&&answer.__slow?answer:{value:answer};
  await pause(delayMs);
  if(signal?.aborted) throw new DOMException('aborted','AbortError');
  if(value instanceof Error) throw value;
  return structuredClone(value);
}
const slow=(value,delayMs)=>({__slow:true,value,delayMs});
const echoNotes=(view,notes)=>({...view,findings:view.findings.map(f=>({...f,note:notes[f.finding_id]??null}))});
const take=(method,fallback)=>queues[method]?.length?queues[method].shift():fallback;
let reviewDefault=decision=>decision.action==='continue'?F.continueTurn(echoNotes(F.caseView(),decision.notes)):decision.action==='remap'?F.remapTurn:F.cancelTurn;
caseApi.createCase=(text,signal)=>{calls.push({method:'createCase',text,signal});return fakeStream(take('createCase',F.descriptionTurn),signal);};
caseApi.sendMessage=(caseId,text,signal,wholeDescription=false)=>{calls.push({method:'sendMessage',caseId,text,signal,wholeDescription});return fakeStream(take('sendMessage',F.reanalysisTurn),signal);};
caseApi.review=(caseId,decision,signal)=>{calls.push({method:'review',caseId,decision,signal});return fakeStream(take('review',reviewDefault(decision)),signal);};
caseApi.updateFindings=(caseId,edits,notes,signal)=>{calls.push({method:'updateFindings',caseId,edits,notes});const answer=take('updateFindings',F.caseView());return fakeJson(answer&&answer.__slow?{...answer,value:echoNotes(answer.value,notes)}:answer instanceof Error?answer:echoNotes(answer,notes),signal);};
caseApi.getCase=(caseId,signal)=>{calls.push({method:'getCase',caseId});return fakeJson(take('getCase',F.caseView()),signal);};
/* Fake case list (T116): tests set serverList; rename and delete edit it. */
let serverList=[];const listCalls=[];
caseApi.listCases=signal=>{listCalls.push(1);return fakeJson(take('listCases',()=>serverList),signal);};
caseApi.renameCase=(caseId,title,signal)=>{calls.push({method:'renameCase',caseId,title});serverList=serverList.map(c=>c.case_id===caseId?{...c,title}:c);return fakeJson(take('renameCase',F.caseView({case_id:caseId,title})),signal);};
caseApi.deleteCase=(caseId,signal)=>{calls.push({method:'deleteCase',caseId});const answer=take('deleteCase',null);if(!(answer instanceof Error))serverList=serverList.filter(c=>c.case_id!==caseId);return fakeJson(answer,signal);};
const listed=(case_id,title,status='idle')=>({case_id,title,created_at:'2026-11-24T00:00:00Z',updated_at:'2026-11-24T00:00:00Z',status});
caseApi.explanations=(caseId,signal)=>{calls.push({method:'explanations',caseId,signal});return fakeJson(take('explanations',F.explanations()),signal);};
caseApi.passage=(chunkId,signal)=>{calls.push({method:'passage',chunkId});return fakeJson(take('passage',{...F.passage,chunk_id:Number(chunkId),ref:`CHUNK:${chunkId}`}),signal);};
const apiError=(status,code,message='')=>new ApiError(status,code,message);

function streamOf(chunks){return new ReadableStream({start(controller){for(const chunk of chunks)controller.enqueue(chunk);controller.close();}});}
async function collect(iterable){const out=[];for await(const item of iterable)out.push(item);return out;}

const {store,sagaTask}=createAppStore();
const sample=(scenario='prenatal',locale='vi')=>store.dispatch(workspaceActions.sampleLoaded({text:sampleCases[scenario][locale]}));
const request=id=>analysisActions.requested({requestId:id,text:store.getState().workspace.description});
const settled=()=>!store.getState().chat.pending&&!selectBusy(store.getState());
async function analyze(id){store.dispatch(request(id));await until(()=>store.getState().analysis.requestId===id&&['review','error','complete','idle'].includes(store.getState().analysis.status));await pause(10);}
async function retrieve(id){store.dispatch(analysisActions.suggestionsRequested({requestId:id}));await until(()=>store.getState().analysis.requestId===id&&['complete','error','idle'].includes(store.getState().analysis.status));await pause(10);}
async function ask(id,text){store.dispatch(chatActions.requested({requestId:id,text}));await until(()=>!store.getState().chat.pending);await pause(10);}
const note=(id,text,analysisId=store.getState().phenotype.analysisRun.id)=>store.dispatch(phenotypeActions.noteSaved({analysisId,id,text}));
const edit=(id,fields,analysisId=store.getState().phenotype.analysisRun.id)=>store.dispatch(phenotypeActions.editSaved({analysisId,id,edit:fields}));
const lastCall=method=>callsOf(method).at(-1);

try {
 /* ---- Task 1: SSE parser and adapters ---- */
 const encoder=new TextEncoder();
 const raw='event: step\ndata: {"id":"a","label":"Tách biểu hiện"}\n\n: ping\n\nevent: text\r\ndata: {"delta":\r\ndata: "xin chào"}\r\n\r\nevent: done\ndata: {"ok":true}\n\n';
 const bytes=encoder.encode(raw);
 const multibyte=raw.indexOf('Tách')+2; // inside "á" (2 bytes in UTF-8)
 const splitAt=[5,encoder.encode(raw.slice(0,multibyte)).length-1,encoder.encode(raw.slice(0,raw.indexOf('\r\n\r\n'))).length+2,bytes.length-3];
 const chunks=[];let last=0;for(const at of splitAt){chunks.push(bytes.slice(last,at));last=at;}chunks.push(bytes.slice(last));
 const parsed=await collect(parseSse(streamOf(chunks)));
 check('SSE parser joins chunks split mid-line, mid-UTF-8 and mid-separator; skips comments; joins multi-line data',()=>{
   assert.deepEqual(parsed.map(e=>e.event),['step','text','done']);
   assert.equal(parsed[0].data.label,'Tách biểu hiện');
   assert.deepEqual(parsed[1].data,{delta:'xin chào'});
 });
 const findings=F.findingViews.map(adapters.toFinding);
 const mapped=adapters.toResult(F.ranking,findings,{analysisId:'run',description:F.DESCRIPTION});
 check('Ranking maps to candidates in rank order with genes, kinds and matched findings; no score',()=>{
   assert.deepEqual(mapped.candidates.map(c=>c.id),['OMIM:190685','ORPHA:2000','OMIM:300000']);
   assert.deepEqual(mapped.candidates.map(c=>c.rank),[1,2.5,2.5]);
   assert.deepEqual(mapped.candidates[1].genes.map(g=>g.symbol),['FGFR2','FGFR3']);
   assert.deepEqual(mapped.candidates[0].matchedFindingIds,['f1']);
   assert.deepEqual(mapped.candidates[1].matchedFindingIds,['f1']);
   assert.deepEqual(mapped.candidates[2].matchedFindingIds,[]);
   assert.equal(mapped.candidates[2].name,'OMIM:300000');
   assert.ok(mapped.candidates.every(c=>!('score' in c)));
   assert.ok(mapped.candidates.every(c=>!c.matchedFindingIds.includes('f2')&&!c.matchedFindingIds.includes('f3')));
   assert.deepEqual(mapped.skipped.map(s=>s.findingId),['f2','f3']);
   assert.equal(mapped.insufficient,false);
 });
 check('Findings carry the English HPO name only (T120), the English definition and the closed candidate set',()=>{
   assert.equal(findings[0].term.label,'Hypoplastic fetal nasal bone');assert.ok(!JSON.stringify(findings).includes('Thiểu sản xương mũi thai'));
   assert.equal(findings[0].term.definition,F.findingViews[0].term.definition_en);
   assert.equal(findings[2].term,null);assert.deepEqual(findings[2].candidates.map(c=>c.id),['HP:0002119','HP:0002118']);
   assert.deepEqual(findings[0].sourceSpan,[14,48]);
 });
 const explained=adapters.applyExplanations(mapped,F.explanations());
 check('Explanations attach claims to the right (disease, query term) pair and every evidence id resolves',()=>{
   const [a,b,c]=explained.candidates;
   assert.equal(a.explanation.pairs[0].status,'cited');assert.equal(a.explanation.pairs[0].claims[0].refs[0],'CHUNK:101');
   assert.deepEqual(a.evidenceIds,['CHUNK:101']);assert.equal(b.explanation.pairs[0].status,'no_passage');assert.equal(c.explanation.pairs[0].status,'failed');
   assert.ok(explained.candidates.every(x=>x.evidenceIds.every(id=>explained.evidence.some(e=>e.id===id))));
   assert.equal(explained.evidence[0].location,'Prenatal diagnosis › Ultrasound markers');assert.equal(explained.explanations,'ready');
 });
 check('Step labels and frequency terms map to i18n keys without colons',()=>{
   assert.equal(adapters.stepLabelKey('tool:analyze_findings'),'step.tool.analyze_findings');assert.equal(adapters.stepLabelKey('split'),'step.node.split');
   assert.equal(adapters.stepSummaryKey('tool:find_passages'),'step.summary.results');assert.equal(adapters.frequencyKey('HP:0040282'),'frequency.HP_0040282');assert.equal(adapters.frequencyKey('3/7'),null);
 });
 check('Progress rows: new id appends, same id updates in place, planning calls are not rows',()=>{
   let items=applyStep([],{id:'a',parent_id:null,kind:'tool',key:'tool:x',status:'running',label:null,params:null,summary:null});
   items=applyStep(items,{id:'t',parent_id:'a',kind:'tool',key:'tool:write_todos',status:'running',label:null,params:null,summary:null});
   items=applyStep(items,{id:'a',parent_id:null,kind:'tool',key:'tool:x',status:'done',label:null,params:null,summary:{results:2}});
   assert.equal(items.length,1);assert.equal(items[0].state,'done');assert.deepEqual(items[0].step.summary,{results:2});
   assert.equal(endTurn([{kind:'step',step:items[0].step,state:'running'}],'review')[0].state,'waiting');
 });

 /* ---- Store and sagas against the fake backend ---- */
 const en=JSON.parse(readFileSync('src/i18n/locales/en.json','utf8')),vi=JSON.parse(readFileSync('src/i18n/locales/vi.json','utf8'));
 check('Translation keys match in both languages',()=>assert.deepEqual(Object.keys(en).sort(),Object.keys(vi).sort()));
 check('The case list is loaded from the server when the app starts',()=>{assert.equal(listCalls.length,1);assert.deepEqual(store.getState().history.entries,[]);});
 check('Edits stay inside the closed set: no action adds, deletes or types an HPO code',()=>{for(const name of ['termReplaced','removed','clinicianFindingAdded','lookupRequested'])assert.equal(phenotypeActions[name],undefined);assert.equal(caseApi.lookupHpo,undefined);});
 check('A fresh workspace cannot retrieve an unanalyzed profile',()=>assert.equal(selectCanRetrieve(store.getState()),false));
 check('New cases start in the composer',()=>assert.equal(selectHasAnalyzed(store.getState()),false));
 sample();
 check('Entering a description does not bypass extraction',()=>assert.equal(selectCanRetrieve(store.getState()),false));
 await analyze('first');
 check('The first message creates the server case from the SSE case event',()=>{assert.equal(callsOf('createCase').length,1);assert.equal(lastCall('createCase').text,sampleCases.prenatal.vi);assert.equal(store.getState().workspace.caseId,'case-1');});
 check('Successful extraction reveals the workspace',()=>assert.equal(selectHasAnalyzed(store.getState()),true));
 check('Successful extraction records the exact analysis source and run',()=>{const run=store.getState().phenotype.analysisRun;assert.equal(run.id,'first');assert.equal(run.description,sampleCases.prenatal.vi);assert.equal(run.completed,true);assert.equal(selectCanRetrieve(store.getState()),true);});
 check('Extraction preserves presence, absence and uncertainty independently',()=>{const f=store.getState().phenotype.findings;assert.equal(f.length,3);assert.equal(f[0].assertion,'PRESENT');assert.equal(f[1].assertion,'ABSENT');assert.equal(f[2].assertion,'UNCERTAIN');assert.equal(f[2].assessment,'LIMITED');});
 check('Step tree: children by parent_id, done updates the same row, review turns running rows into waiting',()=>{
   const items=store.getState().analysis.reviewProgress;
   const ids=items.map(i=>i.step.id);assert.equal(new Set(ids).size,ids.length);
   assert.deepEqual(ids,['call-1','n-extract','n-split','n-choose','n-check']);
   const byId=Object.fromEntries(items.map(i=>[i.step.id,i]));
   assert.equal(byId['n-split'].state,'done');assert.deepEqual(byId['n-split'].step.summary,{items:3});assert.equal(byId['n-split'].step.parent_id,'call-1');
   assert.equal(byId['call-1'].state,'waiting');assert.equal(byId['n-extract'].state,'done');
 });
 check('The follow-up composer is locked while the server waits for review',()=>{assert.equal(store.getState().workspace.serverReview,true);assert.equal(selectCanChat(store.getState()),false);assert.equal(selectCanEdit(store.getState()),true);});
 check('The new server case is the active case and the list is reloaded after the turn',()=>{assert.equal(store.getState().history.activeId,'case-1');assert.ok(listCalls.length>=2);});
 const originalFindings=store.getState().phenotype.findings;
 note('f3','  Cần kiểm tra lại vùng khảo sát.  ');
 check('A note changes context only, not HPO or clinical assertions',()=>{const f=store.getState().phenotype.findings[2];assert.equal(f.note.text,'Cần kiểm tra lại vùng khảo sát.');assert.equal(f.term,originalFindings[2].term);assert.equal(f.sourceText,originalFindings[2].sourceText);assert.equal(f.assertion,'UNCERTAIN');assert.equal(f.assessment,'LIMITED');});
 check('Notes do not require confirmation',()=>assert.equal(selectCanRetrieve(store.getState()),true));
 const revision=store.getState().phenotype.revision;
 note('f3','Cần kiểm tra lại vùng khảo sát.');
 check('Saving unchanged context does not increment the profile revision',()=>assert.equal(store.getState().phenotype.revision,revision));
 check('No request is sent for a note during review',()=>assert.equal(callsOf('updateFindings').length,0));
 edit('f3',{hpo_id:'HP:9999999'});
 check('An HPO code outside the finding\'s candidates is ignored',()=>assert.deepEqual(store.getState().phenotype.edits,{}));
 edit('f1',{assertion:'SUSPECTED'});
 const suspected=store.getState().phenotype.edits.f1;
 edit('f1',{assertion:'PRESENT'});
 edit('f2',{excluded:true,assertion:'ABSENT',assessment:'ASSESSED',subject:'FETUS'});
 edit('f3',{hpo_id:'HP:0002118'});
 check('Review edits keep only fields that differ from the machine reading',()=>{
   assert.deepEqual(suspected,{finding_id:'f1',assertion:'SUSPECTED'});
   const edits=store.getState().phenotype.edits;
   assert.equal(edits.f1,undefined);assert.deepEqual(edits.f2,{finding_id:'f2',excluded:true});assert.deepEqual(edits.f3,{finding_id:'f3',hpo_id:'HP:0002118'});
 });
 respond('review',{events:F.remapTurn,delayMs:25});
 store.dispatch(phenotypeActions.remapRequested({analysisId:'first',requestId:'remap-1',id:'f3',phrase:'giãn não thất'}));
 await until(()=>callsOf('review').length===1);
 check('A running remap locks continue, edits, notes and other remaps',()=>{const s=store.getState();assert.equal(s.analysis.status,'mapping');assert.equal(selectBusy(s),true);assert.equal(selectCanEdit(s),false);assert.equal(selectCanRetrieve(s),false);});
 await until(()=>store.getState().analysis.status==='review');await pause(10);
 check('Remap sends only the finding and its new wording, drops the local term edit, keeps local notes',()=>{
   assert.deepEqual(lastCall('review').decision,{action:'remap',finding_id:'f3',phrase:'giãn não thất'});
   const s=store.getState();assert.equal(s.phenotype.edits.f3,undefined);assert.deepEqual(s.phenotype.edits.f2,{finding_id:'f2',excluded:true});
   const f3=s.phenotype.findings[2];assert.equal(f3.mappingStatus,'MAPPED');assert.equal(f3.term.id,'HP:0002119');assert.equal(f3.note.text,'Cần kiểm tra lại vùng khảo sát.');
   assert.equal(s.analysis.reviewProgress[0].step.key,'remap_one');assert.equal(s.phenotype.analysisRun.id,'first');
 });
 const profileAtContinue=store.getState().phenotype.findings;
 const withNote=F.remappedFindingViews.map(f=>f.finding_id==='f3'?{...f,note:'Cần kiểm tra lại vùng khảo sát.'}:f.finding_id==='f2'?{...f,excluded:true}:f);
 respond('review',{events:F.continueTurn(F.caseView({findings:withNote})),delayMs:15});
 store.dispatch(analysisActions.suggestionsRequested({requestId:'first-result'}));
 await until(()=>store.getState().analysis.status==='generating'&&store.getState().analysis.summaryDraft.length>0);
 const draft=store.getState().analysis.summaryDraft[0].text;
 check('Results-turn text streams into the summary draft',()=>{assert.ok(F.SUMMARY_PARTS.join('').startsWith(draft));assert.ok(draft.length<F.SUMMARY_PARTS.join('').length);});
 await until(()=>store.getState().analysis.status==='complete');await pause(20);
 check('"Tiếp tục" sends the stored edits and notes in POST /review continue',()=>{
   assert.deepEqual(lastCall('review').decision,{action:'continue',edits:[{finding_id:'f2',excluded:true}],notes:{f3:'Cần kiểm tra lại vùng khảo sát.'}});
   const result=store.getState().analysis.result;assert.equal(result.analysisId,'first');assert.equal(result.description,sampleCases.prenatal.vi);
   assert.equal(result.profile.length,profileAtContinue.length);assert.equal(result.profile[2].term.id,'HP:0002119');
 });
 check('Ranking becomes rank-ordered candidates; ABSENT/UNCERTAIN findings are never matched; no score',()=>{const r=store.getState().analysis.result;assert.deepEqual(r.candidates.map(c=>c.rank),[1,2.5,2.5]);assert.deepEqual(r.candidates[1].genes.length,2);assert.ok(r.candidates.every(c=>!('score' in c)&&!c.matchedFindingIds.includes('f2')));});
 check('The frozen summary is the supervisor text of the ranking turn',()=>{const r=store.getState().analysis.result;assert.equal(r.summary,F.SUMMARY_PARTS.join(''));assert.equal(r.summaryFor,r.rankingVersion);assert.equal(r.rankingVersion,1);assert.equal(store.getState().workspace.serverReview,false);});
 await until(()=>store.getState().analysis.result.explanations==='ready');
 check('Explanations are requested after a ranking turn and grounded in their passages',()=>{const r=store.getState().analysis.result;assert.equal(callsOf('explanations').length,1);assert.deepEqual(r.candidates[0].evidenceIds,['CHUNK:101']);assert.ok(r.candidates.every(c=>c.evidenceIds.every(id=>r.evidence.some(e=>e.id===id))));assert.equal(r.candidates[2].explanation.pairs[0].status,'failed');});
 check('A ranking unlocks the follow-up composer and card edits',()=>{assert.equal(selectCanChat(store.getState()),true);assert.equal(selectCanEdit(store.getState()),true);});
 const reviewCalls=callsOf('review').length;
 store.dispatch(analysisActions.suggestionsRequested({requestId:'not-in-review'}));await pause(30);
 check('"Tiếp tục" outside review sends nothing',()=>{assert.equal(callsOf('review').length,reviewCalls);assert.equal(store.getState().analysis.status,'complete');});
 respond('review',{events:F.chatTurn,delayMs:1});
 respond('sendMessage',{events:F.chatTurn,delayMs:2});
 await ask('chat','Vì sao bệnh hạng 1 được gợi ý?');
 check('A follow-up streams progress and text; answer_reset leaves only the replacement; citations stored',()=>{
   const [question,answer]=store.getState().chat.messages;
   assert.equal(question.text,'Vì sao bệnh hạng 1 được gợi ý?');assert.equal(lastCall('sendMessage').caseId,'case-1');
   assert.equal(answer.text,'Bệnh hạng 1 khớp HP:0025707 [CHUNK:101].');
   assert.deepEqual(answer.citations.map(c=>c.id),['HP:0025707','CHUNK:101']);
   assert.ok(answer.progress.every(i=>i.kind!=='step'||i.step.key!=='tool:write_todos'));
   const explain=answer.progress.find(i=>i.kind==='step'&&i.step.key==='tool:explain_disease');assert.equal(explain.step.parent_id,'todo:Giải thích hạng 1');assert.equal(explain.state,'done');
   assert.equal(answer.progress.filter(i=>i.kind==='text').length,1);
 });
 queues.review=[];
 store.dispatch(workspaceActions.evidenceSelected('CHUNK:101'));await pause(20);
 check('A cited passage already in the result opens without a request',()=>{assert.equal(callsOf('passage').length,0);assert.equal(selectSelectedEvidence(store.getState()).evidence.id,'CHUNK:101');});
 store.dispatch(workspaceActions.evidenceSelected('CHUNK:202'));
 check('An uncached chat passage shows a loading state',()=>assert.equal(selectSelectedEvidence(store.getState()).loading,true));
 await until(()=>selectSelectedEvidence(store.getState())?.evidence);
 store.dispatch(workspaceActions.evidenceSelected(null));store.dispatch(workspaceActions.evidenceSelected('CHUNK:202'));await pause(20);
 check('Selecting a chat CHUNK citation fetches the passage once',()=>{assert.equal(callsOf('passage').length,1);assert.equal(lastCall('passage').chunkId,'202');assert.equal(selectSelectedEvidence(store.getState()).evidence.title,'Prenatal markers review');});
 respond('passage',apiError(404,'passage_not_found','gone'));
 store.dispatch(workspaceActions.evidenceSelected('CHUNK:303'));await until(()=>selectSelectedEvidence(store.getState()).gone);
 check('A passage that no longer exists is cached as gone',()=>{assert.equal(selectSelectedEvidence(store.getState()).gone,true);assert.deepEqual(store.getState().analysis.evidenceCache['CHUNK:303'],{gone:true});});
 store.dispatch(workspaceActions.evidenceSelected(null));
 const oldResult=store.getState().analysis.result;
 respond('updateFindings',F.caseView({findings:F.remappedFindingViews.map(f=>f.finding_id==='f3'?{...f,note:'Cần kiểm tra lại số đo.'}:f)}));
 note('f3','Cần kiểm tra lại số đo.');
 await until(()=>callsOf('updateFindings').length===1);await pause(20);
 check('A note after ranking is sent at once with the full set; results stay current; the chat stays',()=>{
   const call=lastCall('updateFindings');assert.deepEqual(call.edits,[{finding_id:'f2',excluded:true}]);assert.deepEqual(call.notes,{f3:'Cần kiểm tra lại số đo.'});
   const s=store.getState();assert.equal(s.chat.messages.length,2);
 });
 check('A note-only update keeps the ranking version, the summary and the explanations',()=>{const r=store.getState().analysis.result;assert.equal(r.rankingVersion,1);assert.equal(r.summaryFor,1);assert.equal(r.explanations,'ready');assert.equal(callsOf('explanations').length,1);});
 check('The previous result object is not mutated',()=>assert.equal(oldResult.profile[2].note.text,'Cần kiểm tra lại vùng khảo sát.'));
 respond('updateFindings',F.caseView({findings:F.remappedFindingViews.map(f=>f.finding_id==='f1'?{...f,assertion:'SUSPECTED',machine:{assertion:'PRESENT'}}:f),ranking:F.rerankedRanking}));
 edit('f1',{assertion:'SUSPECTED'});
 await until(()=>callsOf('updateFindings').length===2);await until(()=>store.getState().analysis.result.rankingVersion===2);
 check('A card edit after ranking re-ranks at once; the summary hides; explanations are requested',()=>{
   assert.deepEqual(lastCall('updateFindings').edits,[{finding_id:'f2',excluded:true},{finding_id:'f1',assertion:'SUSPECTED'}]);
   const s=store.getState();assert.equal(s.analysis.result.candidates[0].id,'ORPHA:2000');assert.notEqual(s.analysis.result.summaryFor,s.analysis.result.rankingVersion);
   assert.equal(s.phenotype.findings[0].assertion,'SUSPECTED');
 });
 await until(()=>callsOf('explanations').length===2);
 respond('updateFindings',slow(F.caseView(),40),F.caseView(),F.caseView());
 note('f3','A');note('f3','B');note('f3','C');
 await until(()=>callsOf('updateFindings').length===4);await pause(60);
 check('Quick successive notes: the last request carries only the latest text',()=>{const sent=callsOf('updateFindings').slice(2);assert.equal(sent.length,2);assert.equal(sent[0].notes.f3,'A');assert.equal(sent[1].notes.f3,'C');});
 queues.updateFindings=[];
 store.dispatch(workspaceActions.descriptionChanged(sampleCases.prenatal.vi+' x'));
 store.dispatch(workspaceActions.descriptionChanged(sampleCases.prenatal.vi+' xy'));
 store.dispatch(workspaceActions.descriptionChanged(sampleCases.prenatal.vi));
 check('Typing archives notes exactly once before clearing the old HPO list',()=>{const p=store.getState().phenotype;assert.equal(p.notesArchive.length,1);assert.equal(p.notesArchive[0].analysisId,'first');assert.equal(p.notesArchive[0].description,sampleCases.prenatal.vi);assert.equal(p.notesArchive[0].findings[0].note.text,'C');assert.equal(p.notesArchive[0].findings[0].assertion,'UNCERTAIN');assert.equal(p.findings.length,0);});
 check('Editing source removes prior results and requires successful reanalysis even after restoring text',()=>{assert.equal(store.getState().analysis.result,null);assert.equal(selectCanRetrieve(store.getState()),false);});
 check('Editing keeps the server conversation visible',()=>assert.equal(store.getState().chat.messages.length,2));
 check('Editing retains the revealed workspace',()=>assert.equal(selectHasAnalyzed(store.getState()),true));
 await analyze('same-hpo');
 check('Reanalysis sends a message on the same case and replaces the HPO list without transferring notes',()=>{assert.equal(callsOf('createCase').length,1);assert.equal(lastCall('sendMessage').caseId,'case-1');assert.equal(lastCall('sendMessage').wholeDescription,true);const p=store.getState().phenotype;assert.equal(p.findings.length,3);assert.ok(p.findings.every(f=>!f.note));assert.equal(p.notesArchive.length,1);assert.equal(p.analysisRun.id,'same-hpo');});
 const beforeStale=store.getState().phenotype;
 note('f3','Old form must not attach','first');
 check('A note submitted from an earlier run cannot attach to the current HPO',()=>assert.equal(store.getState().phenotype,beforeStale));
 note('f1','Nhận xét cho lần thứ hai.');
 respond('sendMessage',apiError(409,'case_waiting_review','waiting'),F.reanalysisTurn);
 await analyze('same-description');
 check('Re-analysing during review cancels the review, then sends the message again; the cancel turn is not shown',()=>{
   const tail=calls.slice(-3).map(c=>c.method+(c.decision?':'+c.decision.action:''));assert.deepEqual(tail,['sendMessage','review:cancel','sendMessage']);
   assert.ok(store.getState().analysis.reviewProgress.every(i=>i.kind!=='text'));assert.equal(store.getState().analysis.status,'review');
 });
 check('Analyzing unchanged text still archives current notes as a separate run',()=>{const p=store.getState().phenotype;assert.equal(p.notesArchive.length,2);assert.equal(p.notesArchive[1].analysisId,'same-hpo');assert.ok(p.findings.every(f=>!f.note));});
 await retrieve('after-archive');
 check('Archived notes never leak into a new continue request',()=>{const d=lastCall('review').decision;assert.equal(d.action,'continue');assert.deepEqual(d.notes,{});assert.deepEqual(d.edits,[]);});
 await until(()=>store.getState().analysis.result.explanations==='ready');
 note('f1','Nhận xét cần giữ nếu lỗi.');await until(()=>callsOf('updateFindings').length===5);
 sample('failure');respond('sendMessage',F.errorTurn);await analyze('failed-run');
 check('A failed reanalysis preserves earlier notes without offering stale continuation',()=>{const s=store.getState();assert.equal(s.analysis.status,'error');assert.equal(s.analysis.error,'error.modelUnavailable');assert.equal(s.phenotype.notesArchive.length,3);assert.equal(s.phenotype.notesArchive[2].findings[0].note.text,'Nhận xét cần giữ nếu lỗi.');assert.equal(selectCanRetrieve(s),false);assert.ok(s.analysis.reviewProgress.every(i=>i.state!=='running'));});
 check('A turn that ended in error asks the server for the case status',()=>assert.equal(lastCall('getCase').caseId,'case-1'));
 sample();await analyze('recover');note('f1','Nhận xét trước khi hủy.');
 respond('sendMessage',{events:[],hang:true});
 store.dispatch(request('cancelled-by-edit'));await until(()=>lastCall('sendMessage').text===store.getState().workspace.description&&callsOf('sendMessage').length>0);await pause(10);
 const abortedBefore=aborted.length;
 store.dispatch(workspaceActions.descriptionChanged('Edited while pending.'));await pause(30);
 check('Cancelling through a source edit aborts the stream, retains notes and blocks a late response',()=>{assert.equal(aborted.length,abortedBefore+1);assert.equal(store.getState().analysis.status,'idle');assert.equal(store.getState().phenotype.findings.length,0);assert.equal(store.getState().phenotype.notesArchive.length,4);assert.equal(store.getState().phenotype.notesArchive[3].findings[0].note.text,'Nhận xét trước khi hủy.');});
 const beforeLate=store.getState().phenotype;
 store.dispatch(phenotypeActions.analysisCompleted({requestId:'cancelled-by-edit',findings}));
 check('A late extraction action cannot repopulate an invalidated source',()=>assert.equal(store.getState().phenotype,beforeLate));
 sample();const abortedOlder=aborted.length;respond('sendMessage',{events:[],hang:true});store.dispatch(request('older'));await pause(10);store.dispatch(request('latest'));await until(()=>store.getState().analysis.requestId==='latest'&&store.getState().analysis.status==='review');
 check('Only the latest analysis can populate the active run; the older stream is aborted',()=>{assert.equal(store.getState().phenotype.analysisRun.id,'latest');assert.equal(store.getState().phenotype.findings.length,3);assert.equal(aborted.length,abortedOlder+1);});
 const archiveCount=store.getState().phenotype.notesArchive.length;
 note('f1','Temporary note');note('f1','');await analyze('cleared-note');
 check('Clearing a note removes only its text and creates no empty archive entry',()=>{assert.equal(store.getState().phenotype.findings.length,3);assert.equal(store.getState().phenotype.notesArchive.length,archiveCount);});
 edit('f2',{excluded:true});
 const forged=analysisActions.suggestionsRequested({requestId:'canonical-context'});forged.payload.findings=[];forged.payload.edits=[];
 respond('review',{events:F.continueTurn(),delayMs:5});
 store.dispatch(forged);store.dispatch(analysisActions.suggestionsRequested({requestId:'double-click'}));
 await until(()=>store.getState().analysis.status==='complete');await pause(20);
 check('Continue uses the store\'s edits and notes, not caller data',()=>assert.deepEqual(lastCall('review').decision.edits,[{finding_id:'f2',excluded:true}]));
 check('"Tiếp tục" twice in review makes exactly one request',()=>assert.equal(callsOf('review').filter(c=>c.decision.action==='continue').length,3));
 await until(()=>store.getState().analysis.result.explanations==='ready');
 const callsBefore=callsOf('review').length;store.dispatch(workspaceActions.descriptionChanged('A different description'));store.dispatch(analysisActions.suggestionsRequested({requestId:'wrong-source'}));await pause(30);
 check('A continue for a changed source is rejected before calling the service',()=>assert.equal(callsOf('review').length,callsBefore));
 sample('empty');respond('sendMessage',F.emptyReviewTurn);await analyze('empty');
 check('A successfully analyzed empty profile can continue without a confirmation gate',()=>{assert.equal(store.getState().analysis.notice,'noFindings');assert.equal(selectCanRetrieve(store.getState()),true);});
 respond('review',F.emptyContinueTurn);await retrieve('empty-result');
 check('An empty analyzed profile yields insufficient evidence without invented candidates',()=>{const r=store.getState().analysis.result;assert.equal(r.insufficient,true);assert.equal(r.candidates.length,0);assert.equal(r.profile.length,0);assert.equal(r.summary,'Chưa có biểu hiện để xếp hạng.');});
 sample('ambiguous');respond('sendMessage',F.ambiguousTurn.slice(1));await analyze('ambiguous');
 check('An ambiguous finding is viewable with its options and does not require manual correction to continue',()=>{const f=store.getState().phenotype.findings[0];assert.equal(f.term,null);assert.equal(f.mappingStatus,'AMBIGUOUS');assert.deepEqual(f.options.map(o=>o.id),['HP:0001627','HP:0001631']);assert.equal(f.assertion,'SUSPECTED');assert.equal(selectCanRetrieve(store.getState()),true);});
 respond('review',F.continueTurn(F.caseView({findings:F.ambiguousFindingViews,ranking:{...F.ranking,suggestions:[]}})));await retrieve('ambiguous-result');
 check('Ambiguous findings do not fabricate a positive disease match',()=>assert.equal(store.getState().analysis.result.insufficient,true));
 sample('prenatal','vi');await analyze('language-switch');
 check('Changing response language preserves the original clinical source',()=>assert.equal(store.getState().phenotype.findings[0].sourceText,'Ghi nhận thiểu sản xương mũi thai.'));
 store.dispatch(workspaceActions.descriptionChanged('HPO là gì?'));respond('sendMessage',F.answerOnlyTurn.slice(1));await analyze('question');
 check('A message answered without findings shows the answer in the review section and cannot continue',()=>{const s=store.getState();assert.equal(s.analysis.notice,'notAnalyzed');assert.equal(selectCanRetrieve(s),false);assert.deepEqual(s.analysis.reviewProgress.filter(i=>i.kind==='text').map(i=>i.text),['HPO là bộ thuật ngữ kiểu hình chuẩn.']);assert.equal(s.analysis.status,'idle');});
 sample();await analyze('before-switch');note('f1','Ghi chú hiện tại.');await retrieve('saved-result');await until(()=>store.getState().analysis.result.explanations==='ready');
 /* ---- Saved cases (T116): list, open, rename, delete ---- */
 const savedView=F.savedCaseView();const snap=snapshotFromView(savedView);
 check('A saved case opens into its description turn, results summary, chat with progress, edits and notes archive',()=>{
   assert.equal(snap.description,'Mô tả gốc của bác sĩ.');assert.equal(snap.title,'Ca đã lưu');assert.equal(snap.id,'case-9');
   assert.deepEqual(snap.reviewProgress.map(i=>i.step.id),['call-1','n-extract','n-split','n-choose','n-check']);
   assert.equal(snap.reviewProgress[0].state,'done');
   assert.equal(snap.result.summary,F.SUMMARY_PARTS.join(''));assert.equal(snap.result.summaryFor,snap.result.rankingVersion);
   assert.deepEqual(snap.chat.map(m=>m.role),['user','assistant']);assert.equal(snap.chat[0].text,'Giải thích thêm');
   assert.equal(snap.chat[1].text,'Bệnh hạng 1 khớp HP:0025707 [CHUNK:101].');assert.equal(snap.chat[1].citations[0].id,'CHUNK:101');
   assert.ok(snap.chat[1].progress.every(i=>i.kind==='text'||i.step.key!=='tool:write_todos'));
   assert.deepEqual(snap.edits,{f2:{finding_id:'f2',assertion:'SUSPECTED'},f3:{finding_id:'f3',excluded:true}});
   assert.equal(snap.findings[0].note.text,'Ghi chú đã lưu.');assert.equal(snap.notesArchive.length,1);assert.equal(snap.notesArchive[0].findings[0].note.text,'Ghi chú cũ.');
   assert.equal(snap.analysisRun.completed,true);assert.equal(snap.reviewPending,false);assert.equal(snap.notice,null);
 });
 check('A card edit that changed the list after the summary hides that summary',()=>assert.equal(snapshotFromView(F.savedCaseView({},true)).result.summaryFor,null));
 check('A first message answered without analysis opens as the description turn with its notice',()=>{const v=snapshotFromView(F.caseView({description:null,findings:[],ranking:null,turns:[F.savedTurn(1,'message',{text:'HPO là gì?'},F.answerOnlyTurn,'done')]}));assert.equal(v.notice,'notAnalyzed');assert.equal(v.chat.length,0);assert.equal(v.description,'HPO là gì?');});
 check('A case reopened while it waits for review shows its findings and no "no findings" notice (T127)',()=>{const v=snapshotFromView(F.caseView({status:'review',ranking:null,turns:[F.savedTurn(1,'message',{text:F.DESCRIPTION},F.descriptionTurn,'review')]}));assert.equal(v.findings.length,3);assert.equal(v.notice,null);assert.equal(v.reviewPending,true);});
 {const {skipLine}=await import('../features/results/skipLine.ts');const words={'assertion.ABSENT':'Không có biểu hiện','subject.FAMILY':'Tiền sử gia đình','skipReason.not_fetal':'không phải biểu hiện của thai','skipReason.absent_checked':'chỉ dùng để đối chiếu'};const tt=(k,o)=>words[k]??o?.defaultValue??k;
 const fam={...adapters.toFinding(F.findingViews[1]),subject:'FAMILY'};
 check('A skipped finding always shows its state next to its reason (T129)',()=>{assert.equal(skipLine({phrase:'bố mẹ cùng huyết thống',hpoId:null,reason:'not_fetal',findingId:'f2',assertion:'ABSENT'},fam,tt),'Không có biểu hiện · không phải biểu hiện của thai');assert.equal(skipLine({phrase:'x',hpoId:null,reason:'lý do cũ',findingId:null,assertion:null},undefined,tt),'lý do cũ');});
 check('A result lists the terms the ranking used with their findings (T129)',()=>{const r=adapters.toResult(F.ranking,[],{analysisId:'a',description:''});assert.deepEqual(r.terms.map(x=>[x.label,x.findingIds]),[['Hypoplastic fetal nasal bone',['f1']]]);assert.equal(r.skipped[0].assertion,'ABSENT');});}
 check('A conflict reaches the candidate with the disease term and its sources (T130)',()=>{const rk=structuredClone(F.ranking);rk.suggestions[0].conflicts=[{query_hpo_id:'HP:0001629',finding_ids:['f2'],disease_hpo_id:'HP:0001629',disease_label_en:'Ventricular septal defect',frequency_raw:['HP:0040282'],evidence:['PCS'],references:[['PMID:1']]}];const r=adapters.toResult(rk,[],{analysisId:'a',description:''});assert.deepEqual(r.candidates[0].conflicts.map(c=>[c.findingIds,c.diseaseLabel,c.references]),[[['f2'],'Ventricular septal defect',[['PMID:1']]]]);assert.deepEqual(r.candidates[1].conflicts,[]);});
check('Two absent findings hitting one disease term are one conflict row',()=>{const rk=structuredClone(F.ranking);rk.suggestions[0].conflicts=[{query_hpo_id:'HP:0001320',finding_ids:['f1'],disease_hpo_id:'HP:0002334',disease_label_en:'Agenesis of cerebellar vermis',frequency_raw:['HP:0040281'],evidence:['TAS'],references:[['ORPHA:1']]},{query_hpo_id:'HP:0002334',finding_ids:['f2'],disease_hpo_id:'HP:0002334',disease_label_en:'Agenesis of cerebellar vermis',frequency_raw:['HP:0040281'],evidence:['TAS'],references:[['ORPHA:1']]}];const r=adapters.toResult(rk,[],{analysisId:'a',description:''});assert.deepEqual(r.candidates[0].conflicts.map(c=>[c.queryHpoIds,c.findingIds,c.diseaseHpoId]),[[['HP:0001320','HP:0002334'],['f1','f2'],'HP:0002334']]);});
 check('A case still waiting for review keeps its waiting row; a resolved one shows it done (T134 (5))',()=>{const v=snapshotFromView(F.caseView({status:'review',ranking:null,turns:[F.savedTurn(1,'message',{text:F.DESCRIPTION},F.descriptionTurn,'review')]}));assert.equal(v.reviewProgress[0].state,'waiting');});
 {const base=adapters.toFinding(F.findingViews[2]);const meas={...base,measurement:{quantity:'AFI',value:'27 cm'},phrase:'ối nhiều'};
 check('A measurement with a conclusion is titled by the clinician\'s words; an undecided one by its quantity (T134 (1))',()=>{assert.deepEqual(adapters.plainTitle({...meas,assertion:'PRESENT'}),{name:'ối nhiều',value:'27 cm'});assert.deepEqual(adapters.plainTitle({...meas,assertion:'UNCERTAIN'}),{name:'AFI',value:'27 cm'});});
 check('"Cần rà soát" counts only findings still waiting for the clinician (T134 (6))',()=>{assert.equal(adapters.needsDoctor(base,undefined),true);assert.equal(adapters.needsDoctor(base,{finding_id:'f3',assertion:'ABSENT'}),false);assert.equal(adapters.needsDoctor({...meas,assertion:'ABSENT'},undefined),false);assert.equal(adapters.needsDoctor({...meas,assertion:'UNCERTAIN'},undefined),true);assert.equal(adapters.needsDoctor(adapters.toFinding(F.findingViews[0]),undefined),false);});}
 serverList=[listed('case-9','Ca đã lưu'),listed('case-1','Ca đang làm')];store.dispatch(historyActions.loadRequested());await until(()=>store.getState().history.entries.length===2);
 respond('getCase',savedView);const exCalls=callsOf('explanations').length;
 store.dispatch(historyActions.openRequested('case-9'));await until(()=>store.getState().workspace.caseId==='case-9');await until(()=>callsOf('explanations').length===exCalls+1);
 check('Opening a saved case restores every area and asks for its (cached) explanations',()=>{const s=store.getState();assert.equal(s.history.activeId,'case-9');assert.equal(s.workspace.title,'Ca đã lưu');assert.equal(s.workspace.description,'Mô tả gốc của bác sĩ.');assert.equal(s.analysis.status,'complete');assert.equal(s.chat.messages.length,2);assert.deepEqual(s.phenotype.edits,snap.edits);assert.equal(s.phenotype.notesArchive.length,1);assert.equal(lastCall('explanations').caseId,'case-9');});
 const renameRevision=store.getState().phenotype.revision;store.dispatch(workspaceActions.titleChanged('Ca đã mở lại'));await until(()=>lastCall('renameCase')?.title==='Ca đã mở lại');
 check('Header renaming sends the title for the open case and does not invalidate analysis',()=>{assert.equal(lastCall('renameCase').caseId,'case-9');assert.equal(store.getState().phenotype.revision,renameRevision);});
 store.dispatch(historyActions.renamed({id:'case-1',title:'  Hồ sơ   theo dõi  '}));await until(()=>lastCall('renameCase')?.caseId==='case-1');
 check('Renaming another case in the list sends it without touching the open case',()=>{assert.equal(lastCall('renameCase').title,'Hồ sơ theo dõi');assert.equal(store.getState().workspace.title,'Ca đã mở lại');});
 store.dispatch(workspaceActions.reset());store.dispatch(workspaceActions.titleChanged('Ca mới có tên'));
 sample('ambiguous');respond('createCase',{events:F.ambiguousTurn.map(e=>e.event==='case'?{event:'case',data:{case_id:'case-2'}}:e)});const renames=callsOf('renameCase').length;await analyze('second-case');await until(()=>callsOf('renameCase').length===renames+1);
 check('A title typed before the first message is sent once the case exists',()=>{assert.deepEqual([lastCall('renameCase').caseId,lastCall('renameCase').title],['case-2','Ca mới có tên']);assert.equal(store.getState().history.activeId,'case-2');});
 store.dispatch(workspaceActions.reset());serverList=[...serverList,listed('case-1','Tiêu đề máy chủ')].filter((c,i,a)=>a.findIndex(x=>x.case_id===c.case_id)===i).map(c=>c.case_id==='case-1'?{...c,title:'Tiêu đề máy chủ'}:c);
 sample();await analyze('default-title');await until(()=>store.getState().workspace.title==='Tiêu đề máy chủ');
 check('A new case shows the default title the server gave it',()=>assert.equal(store.getState().workspace.title,'Tiêu đề máy chủ'));
 respond('sendMessage',{events:[],hang:true});respond('getCase',savedView);
 const abortedBeforeSwitch=aborted.length;
 store.dispatch(request('switch-race'));await pause(10);store.dispatch(historyActions.openRequested('case-9'));await until(()=>store.getState().workspace.caseId==='case-9');await pause(20);
 check('Switching cases cancels pending work and aborts its stream',()=>{assert.equal(aborted.length,abortedBeforeSwitch+1);assert.equal(store.getState().history.activeId,'case-9');assert.equal(store.getState().analysis.requestId,null);});
 const beforeMissing=store.getState().workspace;respond('getCase',apiError(404,'case_not_found','gone'));store.dispatch(historyActions.openRequested('case-gone'));await until(()=>store.getState().analysis.error==='notice.caseExpired');
 check('Opening a case deleted elsewhere tells the doctor and keeps the open case',()=>assert.equal(store.getState().workspace,beforeMissing));
 respond('deleteCase',apiError(409,'case_busy','busy'));store.dispatch(historyActions.removed('case-1'));await until(()=>lastCall('deleteCase')?.caseId==='case-1'&&store.getState().analysis.error==='error.caseBusy');
 check('A case busy on the server is not deleted and comes back in the list',()=>assert.ok(store.getState().history.entries.some(e=>e.id==='case-1')));
 store.dispatch(historyActions.removed('case-9'));await until(()=>!store.getState().history.entries.some(e=>e.id==='case-9')&&lastCall('deleteCase').caseId==='case-9');
 check('Deleting the open case clears the workspace',()=>{assert.equal(store.getState().workspace.caseId,null);assert.equal(store.getState().history.activeId,null);});
 store.dispatch(workspaceActions.reset());sample();store.dispatch(request('reset-race'));store.dispatch(workspaceActions.reset());await pause(30);
 check('Reset cancels pending analysis',()=>{assert.equal(store.getState().analysis.status,'idle');assert.equal(store.getState().phenotype.notesArchive.length,0);});
 check('New case resets progressive disclosure',()=>assert.equal(selectHasAnalyzed(store.getState()),false));

 /* ---- Server status, expiry and errors ---- */
 sample();await analyze('e1');
 respond('sendMessage',apiError(404,'case_not_found','gone'));
 store.dispatch(request('expired'));await until(()=>store.getState().analysis.notice==='caseExpired');await pause(10);
 check('An expired case clears the case id and tells the doctor; the next analysis creates a new case',()=>{const s=store.getState();assert.equal(s.workspace.caseId,null);assert.equal(s.analysis.notice,'caseExpired');assert.equal(s.analysis.status,'idle');});
 const creates=callsOf('createCase').length;await analyze('restart');
 check('Analysing after expiry calls POST /cases again',()=>{assert.equal(callsOf('createCase').length,creates+1);assert.equal(store.getState().workspace.caseId,'case-1');});
 respond('sendMessage',apiError(409,'case_busy','busy'));await analyze('busy');
 check('409 case_busy shows the busy message',()=>assert.equal(store.getState().analysis.error,'error.caseBusy'));
 respond('sendMessage',apiError(409,'case_waiting_review','waiting'));
 respond('review',[{event:'step',data:{id:'x',parent_id:null,kind:'tool',key:'tool:analyze_findings',status:'running',label:null,params:null,summary:null}},{event:'review',data:{findings:F.findingViews}}],[{event:'review',data:{findings:F.findingViews}}]);
 await analyze('cancel-twice');
 check('A review that cannot be cancelled ends the analysis with a general error',()=>{assert.equal(store.getState().analysis.error,'common.error');assert.deepEqual(calls.slice(-4).map(c=>c.method+(c.decision?':'+c.decision.action:'')),['sendMessage','review:cancel','review:cancel','getCase']);});
 respond('review',new TypeError('Failed to fetch'));respond('getCase',apiError(502,'unknown','Bad Gateway'));
 await analyze('unreachable-review');store.dispatch(analysisActions.suggestionsRequested({requestId:'unreachable'}));await until(()=>lastCall('getCase')&&store.getState().analysis.error==='common.error');await pause(30);
 check('A continue that never reached the server can be retried once the server is back',()=>{const s=store.getState();assert.equal(s.analysis.status,'review');assert.equal(selectCanRetrieve(s),true);assert.equal(s.analysis.error,'common.error');});
 respond('sendMessage',apiError(422,'invalid_request','cần 1–4000 ký tự'));await analyze('invalid');
 check('422 shows the backend message',()=>{assert.equal(store.getState().analysis.error,'error.invalid');assert.equal(store.getState().analysis.errorMessage,'cần 1–4000 ký tự');});
 await analyze('ok-again');
 respond('review',F.continueTurn());respond('explanations',apiError(404,'not_deployed','Not Found'));await retrieve('no-route');
 await until(()=>store.getState().analysis.result.explanations==='unavailable');
 check('Explanations route not deployed → unavailable, no error',()=>{assert.equal(store.getState().analysis.result.explanations,'unavailable');assert.equal(store.getState().analysis.error,null);});
 respond('explanations',slow(F.explanations({items:[]}),60),F.explanations());
 const exBefore=callsOf('explanations').length;
 store.dispatch(analysisActions.explanationsRequested());await pause(10);store.dispatch(analysisActions.explanationsRequested());
 await until(()=>store.getState().analysis.result.explanations==='ready');await pause(80);
 check('Two explanation requests in a row: only the second response is kept',()=>{assert.equal(callsOf('explanations').length,exBefore+2);assert.equal(callsOf('explanations').at(-2).signal.aborted,true);assert.equal(store.getState().analysis.result.candidates[0].explanation.pairs[0].status,'cited');});
 respond('explanations',apiError(409,'case_busy'),apiError(409,'case_busy'),apiError(409,'case_busy'),apiError(409,'case_busy'));
 store.dispatch(analysisActions.explanationsRequested());
 await until(()=>store.getState().analysis.result.explanations==='idle',260);
 check('Explanations 409 is retried 3 times, then left idle',()=>assert.equal(callsOf('explanations').length,exBefore+6));
 respond('sendMessage',{events:F.chatTurn,delayMs:15});
 store.dispatch(chatActions.requested({requestId:'typing',text:'Giải thích thêm'}));await pause(30);
 store.dispatch(workspaceActions.descriptionChanged(store.getState().workspace.description+' sửa'));
 await until(()=>!store.getState().chat.pending);await pause(10);
 check('Typing in the description during a chat turn does not cancel it',()=>assert.equal(store.getState().chat.messages.at(-1).text,'Bệnh hạng 1 khớp HP:0025707 [CHUNK:101].'));
 sample();await analyze('before-chat-review');respond('review',F.continueTurn());await retrieve('cr-result');
 respond('sendMessage',F.chatReviewTurn);await ask('chat-review',F.NEW_DESCRIPTION);
 check('A chat message with findings ends in review on the merged description: findings replaced, composer locked',()=>{const s=store.getState();assert.equal(lastCall('sendMessage').wholeDescription,false);assert.equal(s.analysis.status,'review');assert.equal(s.chat.pending,false);assert.equal(selectCanChat(s),false);assert.equal(s.phenotype.findings[0].mappingStatus,'AMBIGUOUS');assert.equal(s.workspace.description,F.MERGED_DESCRIPTION);assert.equal(s.analysis.result,null);assert.equal(s.phenotype.analysisRun.id,'chat-review');assert.equal(selectCanRetrieve(s),true);});
 respond('review',F.continueTurn());await retrieve('after-chat-review');
 respond('sendMessage',F.errorTurn);await ask('chat-error','Câu hỏi lỗi');
 check('A chat turn ending in error unlocks the composer and leaves no running row',()=>{const s=store.getState();assert.equal(s.chat.pending,false);assert.equal(s.chat.error,'error.modelUnavailable');assert.ok(s.chat.messages.at(-1).progress.every(i=>i.state!=='running'));assert.equal(selectCanChat(s),true);});
 respond('sendMessage',{events:[],hang:true});store.dispatch(chatActions.requested({requestId:'chat-reset',text:'x'}));await pause(10);store.dispatch(workspaceActions.reset());await pause(20);
 check('A chat turn cancelled by a new case leaves no pending state',()=>assert.equal(store.getState().chat.pending,false));

 /* Status on reopen */
 respond('getCase',F.savedCaseView({status:'review'}));store.dispatch(workspaceActions.reset());store.dispatch(historyActions.openRequested('case-9'));await until(()=>store.getState().workspace.serverReview);
 check('Reopening a case the server holds in review locks the composer',()=>{assert.equal(store.getState().workspace.serverReview,true);assert.equal(selectCanChat(store.getState()),false);assert.equal(store.getState().analysis.status,'review');});
 respond('getCase',F.savedCaseView({status:'idle'}));store.dispatch(workspaceActions.reset());store.dispatch(historyActions.openRequested('case-9'));await until(()=>store.getState().workspace.caseId==='case-9'&&!store.getState().workspace.serverReview);await pause(20);
 check('Reopening a case the server holds idle shows it complete',()=>assert.equal(store.getState().analysis.status,'complete'));

 /* Measurements without a conclusion (T117, T118) */
 const measured={...F.findingViews[2],finding_id:'f3',phrase:'bất thường chỉ số ối',source_text:'Chỉ số ối 26 cm.',assertion:'UNCERTAIN',assessment:'ASSESSED',measurement:{quantity:'Chỉ số ối',value:'26 cm'}};
 check('A measurement reaches the finding with both verbatim parts',()=>assert.deepEqual(adapters.toFinding(measured).measurement,{quantity:'Chỉ số ối',value:'26 cm'}));
 store.dispatch(workspaceActions.reset());sample();respond('createCase',[{event:'case',data:{case_id:'case-m'}},{event:'review',data:{findings:[F.findingViews[0],measured]}}]);await analyze('measure');
 edit('f3',{assertion:'ABSENT'});
 check('"Normal" on a measurement is an ordinary assertion edit',()=>assert.deepEqual(store.getState().phenotype.edits.f3,{finding_id:'f3',assertion:'ABSENT'}));
 respond('review',[{event:'review',data:{findings:[F.findingViews[0],{...measured,phrase:'đa ối',assertion:'PRESENT'}]}}]);
 store.dispatch(phenotypeActions.remapRequested({analysisId:store.getState().phenotype.analysisRun.id,requestId:'remap-m',id:'f3',phrase:' đa ối ',assertion:'PRESENT'}));
 await until(()=>lastCall('review')?.decision.action==='remap');
 check('"Abnormal" in the clinician\'s words is one remap carrying the conclusion',()=>assert.deepEqual(lastCall('review').decision,{action:'remap',finding_id:'f3',phrase:'đa ối',assertion:'PRESENT'}));
 await pause(30);

 sample();await analyze('carry-base');
 {const carried=structuredClone(F.findingViews);carried[0]={...carried[0],assertion:'SUSPECTED',machine:{assertion:'PRESENT'},note:'Nhận xét cho lần thứ hai.'};
 const turn=[...F.reanalysisTurn.slice(0,-1),{event:'review',data:{findings:carried,carry_over:{applied:1,not_applied:1}}}];
 respond('sendMessage',turn);await analyze('carried');
 check('Re-analysis keeps the corrections the server carried over and says how many (T132)',()=>{const s=store.getState();assert.deepEqual(s.phenotype.edits,{f1:{finding_id:'f1',assertion:'SUSPECTED'}});assert.equal(s.phenotype.findings[0].note.text,'Nhận xét cho lần thứ hai.');assert.deepEqual(s.analysis.carryOver,{applied:1,not_applied:1});assert.ok(s.phenotype.notesArchive.every(e=>e.findings.every(f=>f.note?.text!=='Nhận xét cho lần thứ hai.'||f.sourceText!==s.phenotype.findings[0].sourceText)));});}
 const entryFlow=createAppStore();
 entryFlow.store.dispatch(phenotypeActions.analysisStarted({requestId:'entry-failure',text:'Draft'}));
 entryFlow.store.dispatch(phenotypeActions.analysisFailed('entry-failure'));
 check('Failed first analysis leaves the composer visible',()=>assert.equal(selectHasAnalyzed(entryFlow.store.getState()),false));
 entryFlow.store.dispatch(workspaceActions.descriptionChanged('HPO là gì?'));
 respond('createCase',F.answerOnlyTurn);
 entryFlow.store.dispatch(analysisActions.requested({requestId:'entry-question',text:'HPO là gì?'}));
 await until(()=>entryFlow.store.getState().analysis.notice==='notAnalyzed');await pause(10);
 check('A first message answered without findings reveals the review section with the answer and opens the case',()=>{const s=entryFlow.store.getState();assert.equal(selectHasAnalyzed(s),true);assert.equal(s.history.activeId,'case-q');assert.equal(s.workspace.caseId,'case-q');});
 entryFlow.store.dispatch(phenotypeActions.analysisStarted({requestId:'entry-empty',text:'Draft'}));
 entryFlow.store.dispatch(phenotypeActions.analysisCompleted({requestId:'entry-empty',findings:[]}));
 check('Successful zero-finding analysis still reveals the review step',()=>assert.equal(selectHasAnalyzed(entryFlow.store.getState()),true));
 entryFlow.sagaTask.cancel();
 serverList=[];const fresh=createAppStore();check('A fresh session has no clinical state or archived notes',()=>{assert.equal(fresh.store.getState().history.entries.length,0);assert.equal(fresh.store.getState().phenotype.notesArchive.length,0);});fresh.sagaTask.cancel();
} finally {sagaTask.cancel();}
console.log(checks+' behavioral checks passed.');

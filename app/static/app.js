'use strict';
const $ = id => document.getElementById(id);
const scenarios = {
 E: [{sequence:1,speaker:'client',text:'우울해요. 계속 피곤합니다.'},{sequence:2,speaker:'interviewer',text:'지속 기간과 일상생활 영향을 확인해 주세요.'}],
 A: [{sequence:1,speaker:'client',text:'최근 일주일 동안 잠드는 데 한 시간 정도 걸렸어요.'},{sequence:2,speaker:'client',text:'아침에는 피곤해서 업무에 집중하기 어렵습니다.'}],
 B: [{sequence:10,speaker:'client',text:'보통 네 시간 정도 자요.'},{sequence:11,speaker:'interviewer',text:'평소 수면시간을 다시 확인해 주시겠어요?'},{sequence:12,speaker:'client',text:'제가 잘못 말했어요. 보통 여섯 시간 정도 잡니다.',corrects:10}],
 C: [{sequence:1,speaker:'client',text:'최근에는 퇴근 후 집에서 쉬고 있어요.'}],
 D: [{sequence:1,speaker:'client',text:'홍길동 씨는 서울 영등포구에 거주합니다.'},{sequence:2,speaker:'client',text:'연락처는 010-1234-5678이고 이메일은 demo@example.com입니다.'}]
};
let publicDemo = true;
let current = null, session = null, utterances = [], socket = null, job = null, busy = false;
let sessionCursor = null, runCursor = null, lastRequest = null;
let inspectedRun = null, auditRun = null;
const requests = new Map(), replayOrigins = new Map();
const steps = [['기록 처리','INGESTING'],['음성 전사 (해당 시)','TRANSCRIBING'],['개인정보 치환','REDACTING'],['분석 생성','GENERATING'],['근거 검증','VALIDATING'],['의료진 검토','REVIEW_REQUIRED']];
const stateOrder = ['QUEUED', ...steps.map(s=>s[1]), 'COMPLETED'];
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function empty(id,text) { $(id).className='empty compact'; $(id).replaceChildren(el('p',text)); }
function badge(text) { return el('span',text,'badge '+(text==='SUPPORTED'||text==='APPROVED'?'good':text==='REVIEW_REQUIRED'||text==='REQUIRES_CLINICIAN_REVIEW'?'pending':text==='REJECTED'||text==='UNSUPPORTED'||text==='CONTRADICTED'||text==='STALE_EVIDENCE'||text==='PARTIALLY_SUPPORTED'?'bad':'')); }
function statusBadge(id,status) { const node=badge(status);node.id=id;node.setAttribute('role','status');node.setAttribute('aria-live','polite');$(id).replaceWith(node); }
function time(value) { return value ? new Date(value).toLocaleString() : 'Not exposed'; }
function showError(e) { $('error').textContent=e.message; $('error').hidden=false; }
async function request(path,method='GET',body) {
 const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-API-Key':$('key').value,'Idempotency-Key':crypto.randomUUID()},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
 const value=await response.json(); lastRequest=response.headers.get('X-Request-ID');
 if(!response.ok)throw Error(`${value.error?.code||response.status}: ${value.error?.message||'Request failed'}${lastRequest?' · Request '+lastRequest:''}`);
 return value;
}
const api=(path,method,body)=>request('/api'+path,method,body);
function controls() {
 document.querySelectorAll('[data-mutation]').forEach(n=>n.disabled=busy);
 $('approve').disabled=busy||!current||current.status!=='REVIEW_REQUIRED'||current.statements.some(s=>s.current_validation!=='SUPPORTED')||!!current?.clinical_support?.items.some(s=>s.current_validation!=='SUPPORTED');
 $('reject').disabled=busy||!current||current.status!=='REVIEW_REQUIRED';
 for(const id of ['replay','result-refresh'])$(id).disabled=busy||!current;
 for(const id of ['append','infer'])$(id).disabled=busy||!session;
 $('run-open').disabled=busy||!$('runs').value;
 $('session-open').disabled=busy||!$('sessions').value;
 $('sessions-more').disabled=busy||!sessionCursor; $('runs-more').disabled=busy||!runCursor;
 $('job-resume').disabled=busy||!job;
 if(publicDemo)for(const id of ['title','session-create','input','corrects','append','reviewer','reason'])$(id).disabled=true;
 demoGuide();
}
function demoGuide() {
 let step=1,text='샘플을 실행해 분석 결과를 확인하세요.',target='quick-start',label='샘플 실행으로 이동';
 if(current){
  if(['APPROVED','REJECTED'].includes(current.status)){
   step=4;text=auditRun===current.run_id?`검토 결과: ${current.status}. 아래 검토 및 처리 이력에서 검토자와 처리 시각을 확인하세요.`:'검토가 기록되었습니다. 검토 및 처리 이력을 불러오고 있습니다.';target='audit';label='검토 이력 확인';
  }else{
   step=inspectedRun===current.run_id?3:2;target=step===3?'review-panel':'evidence-workspace';label=step===3?'의료진 검토로 이동':'원문 및 근거 확인';
   text=$('approve').disabled&&!busy?'근거 검증 경고를 확인하세요. 승인할 수 없는 결과는 거절하거나 재실행할 수 있습니다.':'근거를 확인한 뒤 의료진 검토에서 승인 또는 거절하세요.';
  }
 }else if(job?.state==='FAILED'){
  step=2;text='분석을 완료하지 못했습니다. 오류를 확인하거나 샘플을 다시 실행하세요.';target='quick-start';label='샘플 다시 실행';
 }else if(busy||(job&&!['FAILED','COMPLETED','REVIEW_REQUIRED'].includes(job.state))){
  step=2;text='요청을 처리하고 있습니다. 처리 상태를 확인한 뒤 원문 근거를 살펴보세요.';target='state';label='처리 상태 확인';
 }
 $('guide-steps').querySelectorAll('li').forEach((node,index)=>{
  if(index===step-1)node.setAttribute('aria-current','step');else node.removeAttribute('aria-current');
 });
 $('next-action').textContent=text;$('next-link').href='#'+target;$('next-link').textContent=label;
}
function evidenceInspected() { inspectedRun=current?.run_id||null;demoGuide(); }
async function act(task) {
 if(busy)return; busy=true; controls(); $('error').hidden=true;
 try { await task(); } catch(e) { showError(e); } finally { busy=false; controls(); }
}
function pipeline(state='IDLE',progress=0) {
 $('state').textContent=state; $('pipeline').replaceChildren();
 const index=stateOrder.indexOf(state), hasSTT=current?.trace.provider_metadata.providers?.stt;
 steps.forEach(([label,key],i)=>{
  let status='waiting'; const rank=stateOrder.indexOf(key);
  if(index>rank)status='completed'; else if(index===rank)status=key==='REVIEW_REQUIRED'?'awaiting review':'processing';
  if(key==='TRANSCRIBING'&&index>rank&&!hasSTT)status='not observed';
  if(state==='FAILED')status='not confirmed';
  if(key==='REVIEW_REQUIRED'&&state==='FAILED')status='blocked';
  if(key==='REVIEW_REQUIRED'&&current?.status!=='REVIEW_REQUIRED'&&current)status='completed';
  const node=el('div',undefined,'step '+status.replaceAll(' ','-')); node.append(el('small',String(i+1).padStart(2,'0')),el('strong',label),el('span',({'waiting':'대기','completed':'완료','awaiting review':'검토 대기','processing':'처리 중','not observed':'관찰되지 않음','not confirmed':'확인되지 않음','blocked':'중단'})[status])); $('pipeline').append(node);
 });
 if(job)$('job-detail').textContent=`Job ${job.id} · ${progress}% 진행 · ${job.attempts ?? '—'} 회 시도${job.error_code?' · '+job.error_code:''}`;
 demoGuide();
}
function clearResult() {
 current=null;
 inspectedRun=null;auditRun=null;
 empty('clinical','분석 결과를 기다리고 있습니다.'); statusBadge('clinical-status','NO RESULT');
 for(const [id,text] of Object.entries({note:'분석 결과를 기다리고 있습니다.',validation:'분석 후 검증 결과가 표시됩니다.',privacy:'완료된 실행을 선택하면 치환 결과를 확인할 수 있습니다.',audit:'선택된 결과가 없습니다.',comparison:'재실행하면 실제 결과 차이를 비교할 수 있습니다.'}))empty(id,text);
 statusBadge('note-status','AWAITING INFERENCE'); statusBadge('review-status','NO RESULT'); $('review-warning').textContent='분석이 완료되면 검토할 수 있습니다.';
 $('evidence-detail').textContent='결과를 선택하면 원문 근거와 검증 이유를 확인할 수 있습니다.'; $('trace').replaceChildren(); $('trace-raw').textContent='No run selected.';
}
function resetJob() { if(socket)socket.close(); socket=null; job=null; $('events').replaceChildren(); $('event-count').textContent='0'; $('connection').textContent='WebSocket · idle'; $('job-detail').textContent='실제 처리 상태를 표시합니다.'; pipeline(); }
function selectEvidence(statement) {
 evidenceInspected();
 document.querySelectorAll('.highlight,.statement.selected').forEach(n=>n.classList.remove('highlight','selected'));
 $('statement-'+statement.id)?.classList.add('selected');
 statement.evidence.forEach(seq=>$('source-'+seq)?.classList.add('highlight'));
 const box=$('evidence-detail'); box.replaceChildren(badge(statement.current_validation),el('p',statement.reason));
 box.append(el('small',`저장된 검증: ${statement.validation} · 현재 검증: ${statement.current_validation}`));
 if(statement.validation!==statement.current_validation)box.append(el('p','원문 정정으로 현재 검증 상태가 변경되었습니다. 위 설명은 최초 검증 시점의 기록입니다.'));
 if(!statement.evidence.length)box.append(el('p','연결된 원문 근거가 없습니다.'));
 statement.evidence.forEach(seq=>{
  const source=current.source.find(u=>u.sequence===seq);
  box.append(el('blockquote',`#${seq} · ${source?source.text:'저장된 원문에 이 참조가 없습니다.'}`));
 });
 $('source-'+statement.evidence[0])?.scrollIntoView({block:'nearest',behavior:'smooth'});
}
function sources() {
 const box=$('transcript');box.className='transcript-list';box.replaceChildren();
 if(!utterances.length)empty('transcript','원문이 없습니다. 기록 선택 영역에서 가상 발언을 추가하세요.');
 utterances.forEach(u=>{
  const row=el('button',undefined,'utterance'+(u.superseded_by?' stale':''));row.id='source-'+u.sequence;
  row.append(el('small',`#${u.sequence} · ${u.speaker} · ${u.superseded_by?'SUPERSEDED → #'+u.superseded_by:'ACTIVE'}`),el('span',u.text));
  row.onclick=()=>{
   evidenceInspected();
   document.querySelectorAll('.highlight,.statement.selected').forEach(n=>n.classList.remove('highlight','selected'));row.classList.add('highlight');
   const linked=current?.statements.filter(s=>s.evidence.includes(u.sequence))||[];
   linked.forEach(s=>$('statement-'+s.id)?.classList.add('selected'));
   $('evidence-detail').replaceChildren(el('strong',`원문 #${u.sequence} · 연결된 문장 ${linked.length}개`),...linked.map(s=>el('p',`${s.current_validation}: ${s.text}`)));
  };box.append(row);
 });
 const corrections=utterances.filter(u=>u.superseded_by);$('corrections').className='correction-list';$('corrections').replaceChildren();
 if(!corrections.length)empty('corrections','명시적인 정정 연결이 없습니다. 발언 내용만으로 정정을 추정하지 않습니다.');
 corrections.forEach(u=>{const next=utterances.find(x=>x.sequence===u.superseded_by);const card=el('div',undefined,'correction');card.append(el('small',`정정 연결 · #${u.sequence} → #${u.superseded_by}`),el('del',u.text),el('span','↓ 명시적으로 정정된 발언'),el('strong',next?.text||'원문을 불러올 수 없습니다.'),el('small',next?.superseded_by?'이 발언도 이후 정정되었습니다.':'현재 유효한 원문'));$('corrections').append(card);});
}
function markedText(text) {
 const n=el('span');text.split(/(\[[A-Z_]+(?:_\d+)?\])/g).forEach(part=>n.append(el(/^\[[A-Z_]+(?:_\d+)?\]$/.test(part)?'mark':'span',part)));return n;
}
function privacy(result) {
 const table=el('table');const head=el('tr');['원문 번호','현재 원문','저장된 치환 결과'].forEach(t=>head.append(el('th',t)));table.append(head);
 result.source.forEach(u=>{const live=utterances.find(x=>x.sequence===u.sequence);const row=el('tr');row.append(el('td','#'+u.sequence),el('td',live?.text||'현재 기록에 없는 원문입니다.'));const cell=el('td');cell.append(markedText(u.text));row.append(cell);table.append(row);});
 $('privacy').className='table-scroll';$('privacy').replaceChildren(table);
}
function trace(result) {
 const t=result.trace,p=t.provider_metadata.providers||{};
 const fields={'Run ID':result.run_id,'Job ID':result.job_id,'Note ID':result.note_id,'Review status':result.status,'Model identifier':t.model_identifier,'Model version':t.model_version,'Prompt version':t.prompt_version,'LLM provider':p.llm?`${p.llm.identifier} · ${p.llm.version}`:'Not exposed','STT provider':p.stt?`${p.stt.identifier} · ${p.stt.version} (fixed mock fixture)`:'None stored · transcript input','Started':time(t.started_at),'Completed':time(t.completed_at),'Attempt latency':`${t.latency_ms.toFixed(2)} ms`,'Retry count':t.retry_count,'Schema version':t.schema_version,'Replay origin':replayOrigins.get(result.run_id)||'Not exposed for this run','Result request ID':requests.get(result.run_id)||'Not captured','Input SHA-256':t.input_hash,'Redacted SHA-256':t.redacted_input_hash};
 const support=result.clinical_support;
 if(support)Object.assign(fields,{'Clinical provider':support.provider,'Clinical model identifier':support.model_identifier,'Clinical rule version':support.rule_version,'Clinical generated':time(support.timestamp),'Clinical reviewer':support.reviewer||'Pending','Clinical reviewed at':time(support.reviewed_at),'Clinical review status':support.review_status});
 $('trace').replaceChildren();Object.entries(fields).forEach(([k,v])=>$('trace').append(el('dt',k),el('dd',String(v))));
 $('trace-raw').textContent=JSON.stringify({trace:t,validation_summary:result.validation_summary,clinical_support:result.clinical_support},null,2);
}
function clinicalSupport(result) {
 const support=result.clinical_support;
 if(!support){empty('clinical','이전 실행에는 임상 검토 결과가 저장되어 있지 않습니다. 재실행하면 생성됩니다.');statusBadge('clinical-status','NOT GENERATED');return;}
 statusBadge('clinical-status',support.review_status);
 const box=$('clinical');box.className='input-body';box.replaceChildren();
 const summary=el('div',undefined,'validation-counts clinical-summary');summary.setAttribute('aria-label','임상 검토 요약');
 for(const [label,value] of [['관찰된 신호',support.items.filter(i=>i.kind==='observed_signal').length],['검토 후보',support.items.filter(i=>i.kind==='condition_candidate').length],['추가 확인 필요',support.items.filter(i=>i.kind==='missing_information').length],['검토 상태',support.review_status]]){
  const card=el('div');card.append(el('strong',String(value)),el('span',label));summary.append(card);
 }box.append(summary);
 summary.lastChild.dataset.reviewStatus=support.review_status;
 const columns=el('div',undefined,'clinical-columns'),left=el('div'),right=el('div');columns.append(left,right);box.append(columns);
 const groups={observed_signal:'관찰된 신호',risk_signal:'위험 신호',missing_information:'추가 확인 필요',condition_candidate:'검토 후보',follow_up_question:'후속 질문 제안',next_assessment:'추가 평가 제안',recommendation:'AI 검토 제안'};
 Object.entries(groups).forEach(([kind,title])=>{
  const group=el('section',undefined,'clinical-group'),heading=el('h3',title);heading.id='clinical-heading-'+kind;group.setAttribute('aria-labelledby',heading.id);group.append(heading);
  (['observed_signal','risk_signal','missing_information'].includes(kind)?left:right).append(group);
  const items=support.items.filter(i=>i.kind===kind);
  if(!items.length)group.append(el('p','규칙으로 생성된 항목이 없습니다.','clinical-empty'));
  items.forEach(item=>{
   const view=clinicalPresentation(item,result.source),row=el('div',undefined,'statement');row.dataset.kind=item.kind;
   row.append(el('p',item.text),badge(view.status));
   if(view.requiresReview&&support.review_status==='REQUIRES_CLINICIAN_REVIEW')row.append(badge(item.clinical_status));
   if(view.warning)row.append(badge(view.warning));
   const details=el('details');details.append(el('summary','의미 · 검증 상세'),el('p',view.explanation),el('p',view.validationText),el('p','저장된 검증 설명: '+item.reason));
   const refs=el('div',undefined,'evidence-links');
   view.evidence.forEach(seq=>{
    const button=el('button',`${view.sourceLabel==='Evidence'?'근거':'규칙 입력'} · 원문 #${seq}`,'source-ref');
    button.onclick=()=>{
     evidenceInspected();
     document.querySelectorAll('.highlight,.statement.selected').forEach(n=>n.classList.remove('highlight','selected'));
     view.evidence.forEach(n=>$('source-'+n)?.classList.add('highlight'));
     details.open=true;$('source-'+seq)?.scrollIntoView({block:'nearest',behavior:'smooth'});
    };refs.append(button);
    details.append(el('blockquote',`#${seq} · ${result.source.find(u=>u.sequence===seq).text}`));
   });
   if(view.evidence.length)row.append(refs);
   row.append(details);group.append(row);
  });
 });
 box.append(el('p',support.reviewer?`검토자: ${support.reviewer} · ${time(support.reviewed_at)}`:'의료진 검토 대기 · 아래에서 승인 또는 거절을 기록하세요.','clinical-review-meta'));
 if(support.review_status==='REJECTED')box.lastChild.textContent=`거절됨 · 검토자: ${support.reviewer||'기록 없음'} · ${time(support.reviewed_at)}`;
}
function render(result) {
 if(!current||current.run_id!==result.run_id||current.status!==result.status)auditRun=null;
 current=result;statusBadge('note-status',result.status);statusBadge('review-status',result.status);
 $('note').className='note-list';$('note').replaceChildren();
 result.statements.forEach((s,i)=>{
  const row=el('div',undefined,'statement '+(s.current_validation==='SUPPORTED'?'grounded':'flagged'));row.id='statement-'+s.id;
  const select=el('button',undefined,'statement-select');select.append(el('small',`${String(i+1).padStart(2,'0')} / ${s.kind.toUpperCase()}`),el('span',s.text));select.onclick=()=>selectEvidence(s);row.append(select);
  const links=el('div',undefined,'evidence-links');s.evidence.forEach(seq=>{const b=el('button','#'+seq,'source-ref');b.setAttribute('aria-label',`문장 ${i+1}의 원문 근거 ${seq} 확인`);b.onclick=()=>selectEvidence(s);links.append(b);});links.append(badge(s.current_validation));row.append(links);$('note').append(row);
 });
 const counts={};result.statements.forEach(s=>counts[s.current_validation]=(counts[s.current_validation]||0)+1);
 const summary=el('div',undefined,'validation-counts');[['근거 일치',counts.SUPPORTED||0],['근거 부족',counts.UNSUPPORTED||0],['정정된 근거',counts.STALE_EVIDENCE||0],['모순',counts.CONTRADICTED||0],['부분 일치',counts.PARTIALLY_SUPPORTED||0]].forEach(([label,count])=>{const card=el('div');card.append(el('strong',String(count)),el('span',label));summary.append(card);});
 $('validation').className='validation-body';$('validation').replaceChildren(summary,el('p',`저장된 형식 검증: ${result.validation_summary.schema_validity?'통과':'실패'} · 최초 근거 일치율: ${(result.validation_summary.evidence_coverage*100).toFixed(0)}%. 위 수치는 원문 정정을 반영한 현재 검증 결과입니다.`));
 result.statements.filter(s=>s.current_validation!=='SUPPORTED').forEach(s=>{const b=el('button',`${s.current_validation} · ${s.reason}`,'finding');b.onclick=()=>selectEvidence(s);$('validation').append(b);});
 const unsafe=result.statements.some(s=>s.current_validation!=='SUPPORTED')||!!result.clinical_support?.items.some(s=>s.current_validation!=='SUPPORTED');
 $('review-warning').textContent=result.status!=='REVIEW_REQUIRED'?`검토가 기록되었습니다: ${result.status}. 아래 이력에서 검토자와 처리 시각을 확인하세요.`:unsafe?'승인할 수 없습니다. 현재 유효한 근거가 부족한 항목을 확인한 후 거절하거나 재실행하세요.':'근거 검증을 통과했습니다. 분석 결과와 임상 검토 제안을 확인한 뒤 승인 또는 거절하세요. 승인은 진단·치료 확정이 아닙니다.';
 $('review-warning').className=unsafe?'warning':'review-message';
 clinicalSupport(result);privacy(result);trace(result);controls();
 if(job)pipeline(job.state,job.progress);
}
async function audit(result) {
 const entries=await Promise.all([api(`/audit/${result.note_id}`),api(`/audit/${result.job_id}`)]);
 const rows=entries.flat().sort((a,b)=>new Date(b.created_at)-new Date(a.created_at));$('audit').className='audit-list';$('audit').replaceChildren();
 if(!rows.length)empty('audit','이 결과에 저장된 처리 이력이 없습니다.');
 rows.forEach(a=>{const row=el('div',undefined,'audit-row');row.append(el('strong',a.action),el('span',a.details.reviewer?`검토자: ${a.details.reviewer}`:a.details.replay?'재실행 작업':'분석 작업'),el('time',time(a.created_at)));$('audit').append(row);});
 auditRun=result.run_id;demoGuide();
}
async function readResult(id) {const result=await api(`/runs/${id}`);requests.set(result.run_id,lastRequest);return result;}
async function loadRuns(more=false) {
 const page=await api(`/sessions/${session.id}/runs?limit=20${more&&runCursor?'&cursor='+encodeURIComponent(runCursor):''}`);if(!more)$('runs').replaceChildren(el('option','저장된 실행 결과 선택'));
 if(!more)$('runs').firstChild.value='';page.items.forEach(r=>{const o=el('option',`${r.prompt_version} · ${r.model_version} · ${time(r.created_at)}`);o.value=r.id;$('runs').append(o);});runCursor=page.next_cursor;controls();
}
async function loadSessions(more=false) {
 const page=await api(`/sessions?limit=20${more&&sessionCursor?'&cursor='+encodeURIComponent(sessionCursor):''}`);if(!more){$('sessions').replaceChildren(el('option','기록 선택'));$('sessions').firstChild.value='';}
 page.items.forEach(s=>{const o=el('option',`${s.title} · ${time(s.created_at)}`);o.value=s.id;$('sessions').append(o);});sessionCursor=page.next_cursor;controls();
}
async function openSession(id) {session=await api(`/sessions/${id}`);utterances=session.utterances;clearResult();resetJob();$('session').textContent=session.title;sources();await loadRuns();}
async function refreshSession() {const detail=await api(`/sessions/${session.id}`);utterances=detail.utterances;sources();}
async function follow(initial) {
 if(socket)socket.close();job=initial;current=null;$('events').replaceChildren();$('event-count').textContent='0';pipeline(job.state,job.progress);
 $('health-worker').textContent='Worker · awaiting job activity';
 return new Promise((resolve,reject)=>{
  let done=false,polling=false;const seen=new Set();
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/jobs/${job.id}`);socket=ws;
  const finish=async value=>{if(done)return;done=true;clearInterval(poller);clearTimeout(deadline);ws.close();job=value;pipeline(value.state,value.progress);
   if(value.state==='FAILED'){reject(Error(`작업 실패: ${value.error_code||'처리 상세를 확인하세요'} · ${value.id}`));return;}
   try{const result=await api(`/jobs/${value.id}/result`);requests.set(result.run_id,lastRequest);resolve(result);}catch(e){reject(e);}
  };
  const observe=async(message)=>{
   if(done)return;
   const eventKey=message.event_id||`${message.type}:${message.state}:${message.progress}`;
   if(!seen.has(eventKey)){seen.add(eventKey);const li=el('li');li.append(el('strong',message.state),el('span',`${message.progress}% · ${message.type} · ${message.timestamp?time(message.timestamp):'observed '+new Date().toLocaleTimeString()}`));$('events').append(li);$('event-count').textContent=String(seen.size);}
   job={...job,state:message.state,progress:message.progress};pipeline(job.state,job.progress);
   if(!['QUEUED','FAILED'].includes(message.state))$('health-worker').textContent='Worker · job activity observed '+new Date().toLocaleTimeString();
   if(['REVIEW_REQUIRED','COMPLETED','FAILED'].includes(message.state)){
    try{await finish(await api(`/jobs/${job.id}`));}catch(e){if(!done){done=true;clearInterval(poller);clearTimeout(deadline);ws.close();reject(e);}}
   }
  };
  ws.onopen=()=>{if($('key').value)ws.send(JSON.stringify({api_key:$('key').value}));$('connection').textContent='WebSocket · connected';};
  ws.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type!=='heartbeat')void observe(m);}catch(err){showError(err);}};
  ws.onerror=()=>{$('connection').textContent='WebSocket · unavailable / REST fallback';};
  ws.onclose=()=>{$('connection').textContent=done?'WebSocket · closed after job':'WebSocket · disconnected / REST fallback';};
  const poller=setInterval(async()=>{if(done||polling)return;polling=true;try{const value=await api(`/jobs/${job.id}`);job=value;await observe({...value,type:'REST snapshot'});}catch(e){showError(e);}finally{polling=false;}},3000);
  const deadline=setTimeout(()=>{if(done)return;done=true;clearInterval(poller);ws.close();$('connection').textContent='WebSocket · monitoring stopped';reject(Error('90초가 지나 상태 확인을 중단했습니다. 작업은 계속 실행 중일 수 있습니다. 처리 상태 새로고침을 눌러 다시 확인하세요.'));},90000);
  void observe({...initial,type:'REST accepted'});
 });
}
async function showFinished(result) {await refreshSession();render(result);await audit(result);await loadRuns();$('runs').value=result.run_id;}
function compare(original,replay,metrics) {
 const container=$('comparison');container.className='comparison-body';container.replaceChildren(el('p',`동일한 원본 입력 · ${original.run_id} → ${replay.run_id}`));
 const table=el('table'),head=el('tr');['검증 수치 · 버전','이전 결과','재실행 결과'].forEach(t=>head.append(el('th',t)));table.append(head);
 for(const key of ['prompt_version','model_version','evidence_coverage','unsupported_count','contradiction_count','stale_count','partial_count','statement_count','schema_validity','latency_ms']){
  const a=metrics.original[key],b=metrics.replay[key],row=el('tr',undefined,a!==b?'changed':'');row.append(el('td',key.replaceAll('_',' ')),el('td',typeof a==='number'&&!Number.isInteger(a)?a.toFixed(3):String(a)),el('td',typeof b==='number'&&!Number.isInteger(b)?b.toFixed(3):String(b)));table.append(row);
 }container.append(table,el('h3','문장별 변경 내용'),el('p','항목과 원문 참조를 기준으로 저장된 결과를 비교합니다. 현재 근거 상태는 위 검토 영역에서 확인하세요.'));
 const grid=el('div',undefined,'diff-grid');const remaining=[...replay.statements];
 original.statements.forEach(a=>{
  const index=remaining.findIndex(b=>b.kind===a.kind&&JSON.stringify(b.evidence)===JSON.stringify(a.evidence));const b=index<0?null:remaining.splice(index,1)[0];
  const changed=!b||a.text!==b.text||a.validation!==b.validation;
  const left=el('div',undefined,'diff-card '+(changed?'changed':''));left.append(el('small',b?(changed?'이전 결과 · 변경됨':'이전 결과 · 변경 없음'):'이전 결과 · 삭제됨'),el('p',a.text),badge(a.validation),el('small',a.kind+' · '+a.evidence.map(n=>'#'+n).join(', ')));grid.append(left);
  const right=el('div',undefined,'diff-card '+(changed?'changed':''));right.append(el('small',b?'재실행 결과':'재실행 결과 · 항목 없음'),el('p',b?.text||'이 항목 및 근거 참조에 해당하는 문장이 없습니다.'),...(b?[badge(b.validation)]:[]));grid.append(right);
 });
 remaining.forEach(b=>{grid.append(el('div','일치하는 이전 문장이 없습니다.','diff-card'),Object.assign(el('div',undefined,'diff-card changed'),{}));const n=grid.lastChild;n.append(el('small','재실행 결과 · 추가됨'),el('p',b.text),badge(b.validation));});container.append(grid);
}
async function health() {
 $('health-refresh').disabled=true;
 const results=await Promise.allSettled([request('/health'),request('/ready')]);
 $('health-api').textContent=results[0].status==='fulfilled'?'API · alive':'API · unavailable';
 const ready=results[1];for(const name of ['postgres','redis'])$('health-'+name).textContent=`${name==='postgres'?'PostgreSQL':'Redis'} · ${ready.status==='fulfilled'&&ready.value[name]?'ready':'unconfirmed (readiness failed)'}`;
 $('health-time').textContent='Checked '+new Date().toLocaleTimeString();$('health-refresh').disabled=false;
}
async function runScenario(code) {
 const created=publicDemo?await api(`/demo/scenarios/${code}`,'POST'):await api('/sessions','POST',{title:`예시 ${code} · 가상 상담 기록`});await openSession(created.id);
 if(!publicDemo)await api(`/sessions/${session.id}/transcript`,'POST',{utterances:scenarios[code]});await refreshSession();
 const next=await api(`/sessions/${session.id}/inferences`,'POST',{prompt_version:code==='B'?'note-v1':'note-v2',model_version:code==='C'?'mock-unsupported':'mock-v1'});await showFinished(await follow(next));
}
$('start').onclick=()=>act(()=>runScenario($('scenario').value));
$('quick-start').onclick=()=>act(()=>runScenario('E'));
$('session-create').onclick=()=>act(async()=>{const created=await api('/sessions','POST',{title:$('title').value});await openSession(created.id);await loadSessions();$('sessions').value=created.id;});
$('sessions-refresh').onclick=()=>act(()=>loadSessions());$('sessions-more').onclick=()=>act(()=>loadSessions(true));$('runs-more').onclick=()=>act(()=>loadRuns(true));
$('sessions').onchange=controls;$('runs').onchange=controls;
$('session-open').onclick=()=>act(()=>openSession($('sessions').value));
$('run-open').onclick=()=>act(async()=>{clearResult();resetJob();const result=await readResult($('runs').value);job=await api(`/jobs/${result.job_id}`);await showFinished(result);});
$('append').onclick=()=>act(async()=>{
 const lines=$('input').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);if(!lines.length)throw Error('가상 발언을 한 줄 이상 입력하세요.');
 if($('corrects').value&&lines.length!==1)throw Error('정정할 내용은 한 줄로 입력하세요.');
 const last=Math.max(0,...utterances.map(u=>u.sequence));const input=lines.map((text,i)=>({sequence:last+i+1,speaker:'client',text,...($('corrects').value?{corrects:Number($('corrects').value)}:{})}));
 await api(`/sessions/${session.id}/transcript`,'POST',{utterances:input});await refreshSession();$('input').value='';$('corrects').value='';if(current){render(await readResult(current.run_id));await audit(current);}
});
$('infer').onclick=()=>act(async()=>{clearResult();const next=await api(`/sessions/${session.id}/inferences`,'POST',{prompt_version:$('prompt').value,model_version:$('model').value});await showFinished(await follow(next));});
$('job-resume').onclick=()=>act(async()=>{const next=await api(`/jobs/${job.id}`);await showFinished(await follow(next));});
$('result-refresh').onclick=()=>act(async()=>{await refreshSession();const result=await readResult(current.run_id);job=await api(`/jobs/${result.job_id}`);render(result);await audit(result);});
for(const action of ['approve','reject'])$(action).onclick=()=>act(async()=>{
 if(!$('reviewer').checkValidity()||!$('reviewer').value.trim()||!$('reason').value.trim())throw Error('검토자 별칭(영문, 숫자, 밑줄 또는 하이픈)과 검토 사유를 입력하세요.');
 const result=await api(`/runs/${current.run_id}/reviews`,'POST',{action,reviewer:$('reviewer').value,reason:$('reason').value});job=await api(`/jobs/${result.job_id}`);render(result);await audit(result);
});
$('replay').onclick=()=>act(async()=>{
 const original=current;const next=await api(`/runs/${original.run_id}/replay`,'POST',{prompt_version:$('replay-prompt').value,model_version:$('replay-model').value});
 clearResult();const replay=await follow(next);replayOrigins.set(replay.run_id,original.run_id);await showFinished(replay);const metrics=await api(`/comparisons?original=${original.run_id}&replay=${replay.run_id}`);compare(original,replay,metrics);
});
$('health-refresh').onclick=()=>void health();pipeline();controls();void health();
document.querySelectorAll('a[href^="#"]').forEach(link=>link.addEventListener('click',()=>{
 const target=document.getElementById(link.hash.slice(1));
 if(target?.tagName==='DETAILS')target.open=true;
}));

void api('/demo/config').then(config=>{
 publicDemo=config.public_demo_mode;
 for(const id of ['title','input','corrects','reviewer','reason'])$(id).disabled=publicDemo;
 if(publicDemo){
  const notice=el('p','공개 데모에서는 가상 예시 기록만 사용할 수 있습니다.','panel-copy');
  $('advanced-settings').before(notice);
  for(const id of ['title','input','corrects','reviewer','reason'])$(id).closest('label').hidden=true;
  for(const id of ['session-create','append'])$(id).hidden=true;
 }
 controls();
}).catch(showError);

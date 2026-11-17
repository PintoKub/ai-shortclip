// AI ShortClip dashboard — 5 tabs: เนื้อเรื่อง → ซีรีส์ → ผลิตตอน → Agents → ตั้งค่า
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>'$'+Number(n||0).toFixed(2);const epName=e=>`EP.${e.number}${e.variant?'v'+e.variant:''}`;
const media=p=>'/media?path='+encodeURIComponent(p);
const STEP_TH={writer:'เขียนบท',sound:'ทำเสียง',director:'แบ่งช็อต',animator:'ทำภาพ',editor:'ตัดต่อ',qa:'ตรวจ QA'};
const HOOK_TH={countdown:'นับถอยหลัง',forbidden:'ข้อห้าม',midscene:'เปิดกลางเหตุการณ์',lie:'ประโยคหลอก'};
const S={tab:'story',plan:null,planData:null,series:null,seriesList:[],eps:[],jobs:[],ep:null,stage:null,voices:null,typing:false,key:'',agents:null,pickC:null};
function toast(m,ms=5000){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms)}
async function api(p){const r=await fetch(p,{credentials:'same-origin'});if(r.status===401){location.href='/login';throw Error('login')}const d=await r.json();if(!r.ok)throw Error(d.error||r.status);return d}
async function post(p,b){const r=await fetch(p,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-ShortClip':'1'},body:JSON.stringify(b||{})});if(r.status===401){location.href='/login';throw Error('login')}const d=await r.json();if(!r.ok)throw Error(d.error||r.status);return d}
const typingOn='onfocus="S.typing=true" onblur="S.typing=false"';
function go(tab,extra){setTimeout(()=>{const t=document.querySelector('.tab.on');t&&t.scrollIntoView({inline:'center',block:'nearest'})},0);Object.assign(S,extra||{});if(S.be&&extra&&'plan' in extra){S.be.edit=null;S.be.sources=null;S.be.regenFor=null}if(extra&&'ep' in extra&&S.pub)S.pub.data=null;S.tab=tab;S.typing=false;S.key='';document.querySelectorAll('nav.tabs .tab').forEach(t=>t.classList.toggle('on',t.dataset.t===tab));render(true)}

// ================= common data =================
async function loadCommon(){const [ser,eps,jobs,plans,ag]=await Promise.all([api('/api/series'),api('/api/episodes'),api('/api/jobs'),api('/api/plans'),api('/api/agents')]);
 S.seriesList=ser;S.eps=eps;S.jobs=jobs;S.plans=plans;S.agents=ag;if(!S.series)S.series=(ser[0]||{}).slug||'';
 $('total').textContent='ใช้โควตา Claude ไป '+money(eps.reduce((a,e)=>a+Number(e.cost_usd||0),0)+plans.reduce((a,p)=>a+Number(p.cost_usd||0),0))+' (ไม่ตัดบัตร)';
 const needs=ag.agents.reduce((a,x)=>a+x.items.filter(i=>i.kind==='need').length,0);const working=ag.agents.filter(a=>a.state==='working').length;
 $('n_agents').innerHTML=working?`<span class="n">${working} ทำงาน</span>`:'';$('n_agents').innerHTML+=needs?`<span class="n warn">${needs} รอคุณ</span>`:'';
 const busyPlans=plans.filter(p=>p.busy_step).length,readyPlans=plans.filter(p=>['concepts_ready','summary_ready','bible_ready'].includes(p.status)).length;
 $('n_story').innerHTML=(busyPlans?`<span class="n">${busyPlans}</span>`:'')+(readyPlans?`<span class="n warn">${readyPlans}</span>`:'');}
async function refresh(force){try{await loadCommon();await render(force)}catch(e){if(e.message!=='login')toast('โหลดไม่ได้: '+e.message)}}
async function render(force){if(S.typing&&!force)return;const v=$('view');
 const html=S.tab==='story'?await storyView():S.tab==='series'?await seriesView():S.tab==='produce'?await produceView():S.tab==='agents'?agentsView():S.tab==='accounts'?await accountsView():await settingsView();
 if(html===null)return; // view decided nothing changed
 v.innerHTML=html}

function modelPicker(id,cur,note){const C=(S.models||{}).choices||[];return `<div><label>AI ที่ใช้${note?' ('+note+')':''}</label><select id="${id}" style="width:100%">${C.map(c=>`<option value="${esc(c.id)}" ${c.id===cur?'selected':''}>${esc(c.label)}</option>`).join('')}</select></div>`}
const roleModel=r=>(((S.models||{}).roles||{})[r]||{}).model;
// ================= ① เนื้อเรื่อง (idea → concepts → choose → summary) =================
const PLAN_STEP={new:0,drafting_concepts:1,concepts_ready:1,refining:2,summary_ready:2,drafting_bible:3,bible_ready:3,approved:4,failed:-1};
async function storyView(){
 if(S.plan==null){const open=(S.plans||[]).find(p=>p.status!=='approved');if(open)S.plan=open.id;}
 let p=null;if(S.plan){p=await api('/api/plan/'+S.plan);S.planData=p}
 const key=JSON.stringify([S.tab,S.plan,p&&[p.status,p.busy_step,p.alive,p.quota_wait,p.updated_at],S.pickC]);if(key===S.key)return null;S.key=key;
 const list=(S.plans||[]).map(x=>`<div class="card click ${x.id===S.plan?'active':''}" onclick="go('story',{plan:${x.id},pickC:null})"><b class="small">#${x.id} ${esc(x.title||x.idea)}</b><div><span class="status ${x.status==='approved'?'ok':x.busy_step?'run':'wait'}">${planStatus(x)}</span></div></div>`).join('');
 const side=`<div><button class="primary ${S.plan?'':'sel'}" style="width:100%;${S.plan?'':'box-shadow:0 0 0 2px var(--yellow)'}" onclick="go('story',{plan:0})">+ ไอเดียใหม่</button><h3>แผนเรื่องทั้งหมด</h3>${list||'<div class="muted small">ยังไม่มี</div>'}</div>`;
 return `<div class="side">${side}<div>${S.plan?planBody(p):newIdeaForm()}</div></div>`}
function planStatus(x){if(x.busy_step)return 'agent กำลังทำ…';return {new:'ใหม่',drafting_concepts:'คิดคอนเซปต์',concepts_ready:'รอเลือก',refining:'สรุป',summary_ready:'รอยืนยันสรุป',drafting_bible:'สร้าง bible',bible_ready:'รออนุมัติ bible',approved:'เป็นซีรีส์แล้ว',failed:'ล้ม'}[x.status]||x.status}
function planSteps(p){const n=PLAN_STEP[p?p.status:'new'];const st=['ไอเดีย','เลือกคอนเซปต์','สรุปเรื่อง','สร้างซีรีส์ (bible)','พร้อมผลิต'];
 return `<div class="steps">${st.map((t,i)=>`<div class="stp ${i<n||(p&&p.status==='approved')?'done':i===n?'now':'muted'}"><b>${t}</b><span class="small muted">ขั้น ${i+1}/5</span></div>`).join('')}</div>`}
function newIdeaForm(){return `<div class="panel"><h2>สร้างเนื้อเรื่องจากไอเดีย</h2>${planSteps(null)}
 <div class="help">ใส่ไอเดียนิดเดียวพอ Claude จะค้นของจริง (สถานที่ ตำนาน ประวัติศาสตร์) แล้วเสนอคอนเซปต์หลายแบบให้เลือก → คุณเลือก/แก้ → สรุป → แท็บ "ซีรีส์" สร้างตัวละครและตอน</div>
 <label>ไอเดีย <span class="wait">*</span> (สั้น ๆ ก็ได้)</label><textarea id="pIdea" style="min-height:90px" placeholder="เช่น ผีในตำนานไทยมาเรียนมหาลัย · รถไฟสายสุดท้ายที่ไม่มีปลายทาง · นักเรียนทุนติดอยู่ในเกมสมัยอยุธยา" ${typingOn}></textarea>
 <h3>ปรับแต่ง (ไม่บังคับ)</h3><div class="grid g3">
 ${modelPicker('pModel',roleModel('planner'),'ใช้ต่อทุกขั้นของแผนนี้')}<div><label>จำนวนแบบที่อยากได้</label><select id="pCount" style="width:100%"><option>2</option><option selected>3</option><option>4</option><option>5</option></select></div>
 <div><label>แนว</label><input type="text" id="pGenre" placeholder="เช่น สยอง, ลึกลับ, ตลก" ${typingOn}></div>
 <div><label>โทน</label><input type="text" id="pTone" placeholder="เช่น ตลกปนสยอง, ดาร์ก" ${typingOn}></div>
 <div><label>กลุ่มผู้ชม</label><input type="text" id="pAud" placeholder="เช่น วัยรุ่น, นักศึกษา" ${typingOn}></div>
 <div><label>อยากให้อิงสถานที่ไหน</label><input type="text" id="pLoc" placeholder="เช่น กรุงเทพฯ, ลาดกระบัง" ${typingOn}></div>
 <div><label>ต้องมี</label><input type="text" id="pMust" placeholder="เช่น มีหมาเป็นตัวช่วย" ${typingOn}></div></div>
 <div style="margin-top:10px"><label>ห้ามมี</label><input type="text" id="pAvoid" placeholder="เช่น ฉากเลือดเยอะ, การเมือง" ${typingOn}></div>
 <div class="row" style="margin-top:14px"><button class="primary" onclick="newPlan()">ให้ Claude เสนอคอนเซปต์</button><span class="muted small">≈ $0.3–0.6 · 3–6 นาที</span></div></div>`}
async function newPlan(){const b={idea:$('pIdea').value.trim(),model:$('pModel').value,count:$('pCount').value,genre:$('pGenre').value,tone:$('pTone').value,audience:$('pAud').value,location:$('pLoc').value,must:$('pMust').value,avoid:$('pAvoid').value};
 if(!b.idea)return toast('ใส่ไอเดียก่อน');try{const d=await post('/api/plan',b);toast('Claude เริ่มคิดคอนเซปต์แล้ว ดูความคืบหน้าที่แท็บ Agents ได้');S.typing=false;await loadCommon();go('story',{plan:d.plan})}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
function busyBox(p,what){return p.quota_wait?`<div class="gatebox">⏸ ${esc(p.quota_wait)}<div class="small muted">ไม่เสียเงินระหว่างรอ ไม่ต้องกดอะไร</div></div>`:`<div class="help"><span class="spin"></span>${what} — Claude กำลังทำงาน (ดูทีละขั้นได้ที่แท็บ Agents) หน้านี้อัปเดตเอง</div>`}
function planBody(p){const o=p.options||{};let h=`<div class="panel"><div class="row"><h2 class="grow" style="margin:0">แผน #${p.id}</h2><span class="cost">${money(p.cost_usd)}</span></div><div class="row small" style="margin-bottom:6px"><span class="muted">AI ของแผนนี้:</span><select onchange="setPlanModel(${p.id},this.value)" style="padding:3px 6px">${((S.models||{}).choices||[]).map(c=>`<option value="${esc(c.id)}" ${c.id===(o.model||roleModel('planner'))?'selected':''}>${esc(c.label.split(' — ')[0])}</option>`).join('')}</select><span class="muted">(ใช้กับขั้นถัดไปที่กด)</span></div><div class="muted small" style="margin-bottom:8px">ไอเดีย: ${esc(p.idea)} ${['genre','tone','audience','location','must','avoid'].filter(k=>o[k]).map(k=>`<span class="chip">${esc(o[k])}</span>`).join('')}</div>${planSteps(p)}`;
 if(p.status==='failed')h+=`<div class="gatebox" style="border-color:var(--red)"><b class="bad">ล้ม</b><div class="err">${esc(p.error||'')}</div><div class="row" style="margin-top:8px"><button onclick="planAct(${p.id},'concepts',{})">คิดคอนเซปต์ใหม่</button>${p.summary?`<button onclick="go('series',{plan:${p.id}})">ไปสร้าง bible อีกครั้ง</button>`:''}</div></div>`;
 if(p.busy_step&&p.alive)return h+busyBox(p,{concepts:'กำลังค้นของจริงและคิดคอนเซปต์',summary:'กำลังสรุปคอนเซปต์ที่เลือก',bible:'กำลังสร้างตัวละคร ตอน และ lore'}[p.busy_step]||'กำลังทำงาน')+'</div>';
 if(p.busy_step&&!p.alive)h+=`<div class="gatebox" style="border-color:var(--red)">agent หยุดกลางทาง (process หาย) <button onclick="planAct(${p.id},'concepts',{})">ลองใหม่</button></div>`;
 if(p.concepts&&!p.summary){h+=`<h3>② เลือกคอนเซปต์ที่ชอบ (แก้ข้อความในการ์ดได้ก่อนเลือก)</h3><div class="grid ${p.concepts.length>3?'g2':'g3'}">${p.concepts.map(c=>conceptCard(c)).join('')}</div>
  <h3>สิ่งที่อยากปรับตอนสรุป (ไม่บังคับ)</h3><textarea id="cNotes" placeholder="เช่น ให้ตัวเอกเป็นผู้หญิง · ลดความสยองลง · อยากได้ฉากที่ลาดกระบัง" ${typingOn}></textarea>
  <div class="row" style="margin-top:10px"><button class="primary" ${S.pickC?'':'disabled'} onclick="chooseConcept(${p.id})">เลือกแบบนี้ แล้วให้สรุป</button><span class="muted small">≈ $0.2 · 1–3 นาที</span><span class="grow"></span>
  <input type="text" id="cExtra" placeholder="ไม่ชอบเลย? บอกทิศทางใหม่" style="max-width:320px" ${typingOn}><button onclick="planAct(${p.id},'concepts',{extra:$('cExtra').value})">เสนอชุดใหม่</button></div>`}
 if(p.summary){const s=p.summary;h+=`<h3>③ สรุปเรื่อง (แก้ได้ แล้วกดบันทึก)</h3><div class="grid g2">
  ${fld('sTitle','ชื่อซีรีส์',s.title)}${fld('sSlug','รหัสซีรีส์ (a-z0-9-)',s.slug)}${fld('sGenre','แนว',s.genre)}${fld('sTone','โทน',s.tone)}</div>
  ${area('sLog','แก่นเรื่อง (logline)',s.logline)}${area('sPrem','โลกและปมหลัก',s.premise)}${area('sArc','เส้นเรื่องทั้งซีซัน',s.season_arc)}${area('sMys','คำถามใหญ่ของซีซัน',s.core_mystery)}
  ${s.selling_points&&s.selling_points.length?`<h3>จุดขาย</h3>${s.selling_points.map(x=>`<span class="chip">${esc(x)}</span>`).join('')}`:''}
  ${s.real_anchor?`<h3>อิงของจริง</h3><div class="small">${esc(s.real_anchor)}</div>`:''}
  ${s.open_questions&&s.open_questions.length?`<h3>เรื่องที่ควรตัดสินก่อนสร้างตัวละคร</h3>${s.open_questions.map(x=>`<div class="small">• ${esc(x)}</div>`).join('')}`:''}
  <div class="row" style="margin-top:14px"><button onclick="saveSummary(${p.id})">บันทึกสรุป</button>${p.status==='approved'?`<button class="good" onclick="go('produce',{series:'${esc(p.series_slug)}'})">ไปผลิตตอน →</button>`:`<button class="primary" onclick="saveSummary(${p.id},true)">ใช้สรุปนี้ → ไปสร้างตัวละครและตอน</button>`}
  <button onclick="if(confirm('กลับไปเลือกคอนเซปต์ใหม่?'))planAct(${p.id},'concepts',{})">กลับไปเลือกคอนเซปต์ใหม่</button></div>`}
 return h+'</div>'}
const fld=(id,l,v)=>`<div><label>${l}</label><input type="text" id="${id}" value="${esc(v||'')}" ${typingOn}></div>`;
const area=(id,l,v)=>`<div style="margin-top:8px"><label>${l}</label><textarea id="${id}" ${typingOn}>${esc(v||'')}</textarea></div>`;
function conceptCard(c){const sel=S.pickC===c.id;return `<div class="card pick ${sel?'sel':''}"><div class="row"><b class="grow" contenteditable="true" data-c="${c.id}" data-k="title" ${typingOn}>${esc(c.title)}</b><button onclick="S.pickC='${c.id}';S.key='';render(true)">${sel?'✓ เลือกแล้ว':'เลือก'}</button></div>
 <div class="small muted">${esc(c.genre)} · ${esc(c.tone)} · ${esc(c.audience)}</div>
 <div class="small" style="margin-top:6px" contenteditable="true" data-c="${c.id}" data-k="logline" ${typingOn}>${esc(c.logline)}</div>
 <div class="small" style="margin-top:6px"><b class="wait">3 วิแรก:</b> <span contenteditable="true" data-c="${c.id}" data-k="hook_3s" ${typingOn}>${esc(c.hook_3s)}</span></div>
 <div class="small" style="margin-top:6px"><b class="ok">ทำไมจะดูต่อ:</b> ${esc(c.why_watch)}</div>
 ${c.real_anchor?`<div class="small" style="margin-top:6px"><b style="color:var(--purple)">อิงของจริง:</b> ${esc(c.real_anchor)}</div>`:''}
 <details style="margin-top:6px"><summary class="small muted">เส้นเรื่อง + ไอเดียตอน</summary><div class="small" contenteditable="true" data-c="${c.id}" data-k="season_arc" ${typingOn}>${esc(c.season_arc)}</div>${c.episode_seeds.map(x=>`<div class="small">• ${esc(x)}</div>`).join('')}${c.risk?`<div class="small bad">ความเสี่ยง: ${esc(c.risk)}</div>`:''}</details></div>`}
async function setPlanModel(id,model){try{await post(`/api/plan/${id}/model`,{model});toast('เปลี่ยน AI ของแผนนี้แล้ว');S.key=''}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function planAct(id,action,body){try{await post(`/api/plan/${id}/${action}`,body);toast('ส่งงานให้ agent แล้ว');S.typing=false;S.key='';S.pickC=null;setTimeout(()=>refresh(true),1200)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
function chooseConcept(id){const c={id:S.pickC};document.querySelectorAll(`[data-c="${S.pickC}"]`).forEach(el=>c[el.dataset.k]=el.innerText.trim());planAct(id,'choose',{concept:c,notes:($('cNotes')||{}).value||''})}
async function saveSummary(id,next){const s={title:$('sTitle').value,slug:$('sSlug').value,genre:$('sGenre').value,tone:$('sTone').value,logline:$('sLog').value,premise:$('sPrem').value,season_arc:$('sArc').value,core_mystery:$('sMys').value};
 try{await post(`/api/plan/${id}/save-summary`,{summary:s});toast('บันทึกสรุปแล้ว');S.typing=false;S.key='';if(next)go('series',{plan:id});else refresh(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}

// ================= ② ซีรีส์ (characters, episodes, lore → approve) =================
async function seriesView(){
 const plans=(S.plans||[]).filter(p=>['summary_ready','drafting_bible','bible_ready','approved','failed'].includes(p.status));
 if(S.plan==null||!plans.find(p=>p.id===S.plan)){const pick=plans.find(p=>p.status!=='approved')||plans[0];S.plan=pick?pick.id:null}
 let p=S.plan?await api('/api/plan/'+S.plan):null;
 const key=JSON.stringify(['series',S.plan,p&&[p.status,p.busy_step,p.alive,p.quota_wait,p.updated_at,p.error],S.be&&[S.be.edit,S.be.regenFor,S.be.pickUrl,S.be.searching,(S.be.hits||[]).length]]);if(key===S.key)return null;S.key=key;
 const side=`<div><h3>แผนที่สรุปแล้ว</h3>${plans.map(x=>`<div class="card click ${x.id===S.plan?'active':''}" onclick="go('series',{plan:${x.id}})"><div class="row"><b class="grow">#${x.id} ${esc(x.title||x.idea)}</b><span class="status">${planStatus(x)}</span></div></div>`).join('')||'<div class="muted small">ยังไม่มี — เริ่มที่แท็บ ① เนื้อเรื่อง</div>'}
  <h3>ซีรีส์ที่มีอยู่</h3>${S.seriesList.map(s=>`<div class="card click" onclick="go('produce',{series:'${esc(s.slug)}',ep:null})"><b>${esc(s.slug)}</b> <span class="muted small">ถึง EP.${s.last_ep}</span></div>`).join('')}</div>`;
 if(!p)return `<div class="side">${side}<div class="panel"><h2>② ซีรีส์</h2><div class="help">ต้องมีสรุปเรื่องจากแท็บ ① ก่อน แล้วค่อยมาสร้างตัวละคร จำนวนตอน และ lore ที่นี่</div></div></div>`;
 let h=`<div class="panel"><div class="row"><h2 class="grow" style="margin:0">${esc((p.summary||{}).title||'')}</h2><span class="cost">${money(p.cost_usd)}</span></div>${planSteps(p)}`;
 if(p.busy_step&&p.alive&&!String(p.busy_step).startsWith('regen'))return `<div class="side">${side}<div>${h}${busyBox(p,'กำลังค้น lore จริง สร้างตัวละครและแผนทุกตอน (5–10 นาที)')}</div></div></div>`;
 if(p.status==='failed')h+=`<div class="gatebox" style="border-color:var(--red)"><b class="bad">ล้ม</b><div class="err">${esc(p.error||'')}</div></div>`;
 const bo=p.bible_opts||{};
 h+=(p.bible?approveBox(p):'')+`${p.bible?'<details><summary class="muted" style="cursor:pointer;margin:10px 0">ไม่ชอบ? ปรับแล้วสร้าง bible ใหม่ (เสียโควตาอีกรอบ)</summary>':'<h3>ตั้งค่าซีรีส์ที่จะสร้าง</h3>'}<div class="grid g3"><div><label>จำนวนตอน</label><input type="number" id="bEps" min="1" max="24" value="${bo.episodes||6}"></div><div><label>เริ่มที่ตอน</label><input type="number" id="bStart" min="1" value="${bo.start_episode||1}"></div><div></div></div>
 ${area('bChars','ตัวละครที่อยากได้ (ชื่อ / บทบาท / ทำอะไรได้ / ทำอะไรไม่ได้) — เว้นว่างให้ Claude ออกแบบ',bo.characters||'')}
 ${area('bAnch','ของจริงที่อยากให้อิง (สถานที่ ตำนาน บุคคลในประวัติศาสตร์) — Claude จะค้นและอ้างแหล่งให้',bo.anchors||'')}
 ${area('bNotes','คำสั่งเพิ่ม',bo.notes||'')}
 <div class="row" style="margin-top:10px"><button class="${p.bible?'':'primary'}" onclick="${p.bible?`if(confirm('สร้าง bible ใหม่ทั้งหมด? (≈ $1)'))`:''}makeBible(${p.id})">${p.bible?'สร้าง bible ใหม่':'สร้างตัวละคร + ตอน + lore'}</button><span class="muted small">≈ $0.5–1.2 · 5–10 นาที · AI: ${esc(((S.models.choices||[]).find(c=>c.id===((p.options||{}).model||roleModel('planner')))||{label:'?'}).label.split(' — ')[0])} (เปลี่ยนได้ที่แท็บ ①)</span></div>${p.bible?'</details>':''}`;
 if(p.bible)h+=bibleView(p)+approveBox(p);
 return `<div class="side">${side}<div>${h}</div></div></div>`}
function approveBox(p){const b=p.bible;return `<div class="gatebox" style="margin-top:16px">${p.status==='approved'?`<b class="ok">อนุมัติแล้ว → ซีรีส์ ${esc(p.series_slug)}</b> <button class="good" onclick="go('produce',{series:'${esc(p.series_slug)}',ep:null})">ไปผลิตตอน →</button>`:
 `<b>อนุมัติ bible นี้เป็นซีรีส์?</b><div class="small muted">ระบบจะสร้างซีรีส์ ใส่กฎ และให้นักเขียนบทใช้ตัวละคร/lore/แผนรายตอนนี้ทุกครั้ง</div><div class="row" style="margin-top:8px"><input type="text" class="bSlug" value="${esc(b.slug)}" style="max-width:240px" ${typingOn}><button class="good" onclick="approvePlan(${p.id},this.previousElementSibling.value)">อนุมัติเป็นซีรีส์</button></div>`}</div>`}
function makeBible(id){planAct(id,'bible',{opts:{episodes:$('bEps').value,start_episode:$('bStart').value,characters:$('bChars').value,anchors:$('bAnch').value,notes:$('bNotes').value}})}
function bibleView(p){const b=p.bible,w=p.warnings||[];
 return `<hr style="border-color:var(--line);margin:18px 0"><h2>Series bible</h2>${w.map(x=>`<div class="small wait">⚠ ${esc(x)}</div>`).join('')}
 <div class="small">${esc(b.logline)}</div>${b.world&&b.world.setting?`<h3>โลก</h3><div class="small">${esc(b.world.setting)} ${b.world.era?'· '+esc(b.world.era):''}</div>${(b.world.rules||[]).map(r=>`<div class="small">• ${esc(r)}</div>`).join('')}`:''}
 <h3>ตัวละคร (${b.characters.length})</h3><div class="grid g2">${b.characters.map(c=>`<div class="card"><div class="row"><b class="grow">${esc(c.name)}</b><span class="chip">${esc(c.role)}</span></div>
  <div class="small muted">${esc(c.age)} · ${esc(c.look)}</div><div class="small" style="margin-top:4px">${esc(c.personality)}</div>
  <div class="small" style="margin-top:4px"><b class="ok">ทำได้:</b> ${c.abilities.map(esc).join(' · ')}</div><div class="small"><b class="bad">ทำไม่ได้:</b> ${c.limits.map(esc).join(' · ')}</div>
  ${c.goal?`<div class="small"><b>เป้าหมาย:</b> ${esc(c.goal)}</div>`:''}${c.secret?`<div class="small"><b>ความลับ:</b> ${esc(c.secret)}</div>`:''}${c.arc?`<div class="small"><b>พัฒนาการ:</b> ${esc(c.arc)}</div>`:''}</div>`).join('')}</div>
 <h3>Lore จากของจริง (${b.lore.length}) — ทุกข้อมีแหล่งที่ค้นจริง</h3>${b.lore.map(x=>`<div class="lore"><b>${esc(x.name)}</b> <span class="chip">${esc(x.kind)}</span> <a href="${esc(x.source_url)}" target="_blank" rel="noopener">แหล่ง</a><div><b class="muted">จริง:</b> ${esc(x.fact)}</div><div><b style="color:var(--purple)">เราแต่งต่อ:</b> ${esc(x.twist)}</div><div class="small muted">ใช้ในตอน ${(x.used_in||[]).join(', ')||'—'}</div></div>`).join('')||'<div class="small muted">—</div>'}
 <h3>แผนรายตอน (${b.episodes.length})</h3>${b.episodes.map(e=>`<div class="ep"><div class="row"><b class="grow">EP.${e.number} ${esc(e.title)}</b><span class="chip">${esc(HOOK_TH[e.hook_type]||e.hook_type)}</span></div>
  <div><b class="wait">3 วิแรก:</b> ${esc(e.hook)}</div><div>${esc(e.summary)}</div><div class="muted">สถานที่: ${esc(e.location)} · ตัวละคร: ${e.characters.map(id=>esc((b.characters.find(c=>c.id===id)||{}).name||id)).join(', ')}</div>
  ${e.twist?`<div><b>หักมุม:</b> ${esc(e.twist)}</div>`:''}${e.cliffhanger?`<div><b class="bad">ค้างไว้:</b> ${esc(e.cliffhanger)}</div>`:''}</div>`).join('')}
 ${(b.story_rules||[]).length+(b.visual_rules||[]).length?`<h3>กฎที่จะใช้ทุกตอน</h3><div class="grid g2"><div><b class="small">เนื้อเรื่อง (นักเขียนบท)</b>${(b.story_rules||[]).map(x=>`<div class="small">• ${esc(x)}</div>`).join('')}</div><div><b class="small">ภาพ (นักวาด)</b>${(b.visual_rules||[]).map(x=>`<div class="small">• ${esc(x)}</div>`).join('')}</div></div>`:''}`}
async function approvePlan(id,slug){if(!confirm('อนุมัติเป็นซีรีส์?'))return;try{const d=await post(`/api/plan/${id}/approve`,{slug});toast('สร้างซีรีส์แล้ว');S.series=d.plan.series_slug;S.typing=false;await loadCommon();go('produce',{ep:null})}catch(e){toast('ผิดพลาด: '+e.message,8000)}}

// ================= ③ ผลิตตอน =================
function jobOf(eid){return S.jobs.find(j=>j.episode_id===eid)}
function epState(e,j){if(j){if(j.status==='waiting_gate')return{cls:'wait',label:j.gate==='gate1_script'?'รออนุมัติบท':'รออนุมัติคลิป'};if(j.status==='running')return{cls:'run',label:'กำลัง'+(STEP_TH[j.current_step]||'ทำงาน')};if(j.status==='queued')return{cls:'run',label:'เข้าคิว'};if(j.status==='failed')return{cls:'bad',label:'ล้ม'};if(j.status==='done')return{cls:'ok',label:'พร้อมปล่อย'};if(j.status==='cancelled')return{cls:'muted',label:'ยกเลิก'}}return{cls:'ok',label:e.status==='qa_ok'?'ส่งแล้ว':e.status}}
async function produceView(){const sid=(S.seriesList.find(x=>x.slug===S.series)||{}).id;const mine=S.eps.filter(e=>e.series_id===sid);
 const ser=await api('/api/series/'+encodeURIComponent(S.series||'x'));
 let detail='';
 if(S.ep==='new'||!mine.length){detail=await newEpForm(ser)}
 else{if(!S.ep||!mine.find(e=>e.episode_id===S.ep))S.ep=mine[mine.length-1].episode_id;
  const [d,f]=await Promise.all([api('/api/episode/'+S.ep),api(`/api/episode/${S.ep}/files`)]);const j=d.jobs[0]||{};
  const key=JSON.stringify(['produce',S.series,S.ep,j.status,j.gate,j.current_step,j.error,d.steps.map(s=>s.status+s.attempt),f.voice,f.web,S.stage,S.stage==='publish'?pubKey():'']);if(key===S.key)return null;S.key=key;
  let sc=null;try{sc=await api(`/api/episode/${S.ep}/script`)}catch(_){}detail=epView(d,f,sc,ser)}
 const planned=(ser.bible&&ser.bible.episodes||[]).filter(e=>!mine.find(m=>m.number===e.number));
 const side=`<div><label>ซีรีส์</label><select style="width:100%" onchange="go('produce',{series:this.value,ep:null})">${S.seriesList.map(s=>`<option ${s.slug===S.series?'selected':''}>${esc(s.slug)}</option>`).join('')}</select>
  <button class="primary" style="width:100%;margin-top:10px" onclick="go('produce',{ep:'new'})">+ ผลิตตอนใหม่</button><h3>ตอนที่ผลิตแล้ว</h3>
  ${mine.slice().reverse().map(e=>{const st=epState(e,jobOf(e.episode_id));return `<div class="card click ${S.ep===e.episode_id?'active':''}" onclick="go('produce',{ep:${e.episode_id},stage:null})"><div class="row"><b class="grow">${epName(e)}</b><span class="status ${st.cls}">${st.label}</span></div><div class="muted small">${esc((e.title||'').split(':').pop().trim())} · ${e.duration?Number(e.duration).toFixed(1)+' วิ':'—'} · <span class="cost">${money(e.cost_usd)}</span></div></div>`}).join('')||'<div class="muted small">ยังไม่มี</div>'}
  ${planned.length?`<h3>ตอนที่วางแผนไว้ใน bible</h3>${planned.map(e=>`<div class="card click" onclick="go('produce',{ep:'new',planEp:${e.number}})"><b>EP.${e.number}</b> <span class="small">${esc(e.title)}</span></div>`).join('')}`:''}</div>`;
 return `<div class="side">${side}<div>${detail}</div></div>`}
async function newEpForm(ser){const key=JSON.stringify(['newep',S.series,S.planEp]);if(key===S.key&&S.typing)return null;S.key=key;
 const last=(S.seriesList.find(x=>x.slug===S.series)||{}).last_ep||0;const b=ser.bible;const n=S.planEp||last+1;const pe=b&&b.episodes.find(e=>e.number===n);
 const rules=(ser.rules.story||[]).concat(ser.rules.visual||[]);
 return `<div class="panel"><h2>ผลิตตอนใหม่ · ${esc(S.series)}</h2><div class="help">กดแล้ว: เขียนบท → <b>หยุดรอคุณอนุมัติบท</b> → เสียงพากย์ → ภาพ → คลิปสุดท้าย <b>รออนุมัติอีกครั้ง</b></div>
 ${pe?`<div class="ep"><b>แผนจาก bible — EP.${pe.number} ${esc(pe.title)}</b><div>${esc(pe.summary)}</div><div class="muted">เปิดด้วย: ${esc(pe.hook)} · ค้างไว้: ${esc(pe.cliffhanger)}</div><div class="small ok">นักเขียนบทจะเขียนตามแผนนี้ ตัวละคร และ lore ของซีรีส์อัตโนมัติ</div></div>`:b?`<div class="small wait">ตอนนี้ไม่อยู่ในแผน bible — นักเขียนจะยังใช้ตัวละคร/lore ของซีรีส์</div>`:''}
 <div class="grid g3"><div><label>ตอนที่</label><input type="number" id="nEp" min="1" value="${n}"></div><div><label>ความยาว (50–70 วิ)</label><input type="number" id="nLen" min="50" max="70" value="60"></div>
 <div><label>แบบเปิดเรื่อง</label><select id="nHook" style="width:100%">${Object.entries(HOOK_TH).map(([k,v])=>`<option value="${k}" ${(pe&&pe.hook_type===k)?'selected':''}>${v}</option>`).join('')}</select></div></div>
 ${area('nIdea',pe?'สิ่งที่อยากเพิ่ม/เปลี่ยนจากแผน (ไม่บังคับ)':'ไอเดียของตอนนี้','')}
 <div class="grid g2" style="margin-top:8px"><div><label>กฎเรื่องเพิ่มเฉพาะตอน (บรรทัดละข้อ)</label><textarea id="nRS" ${typingOn}></textarea></div><div><label>กฎภาพเพิ่มเฉพาะตอน (บรรทัดละข้อ)</label><textarea id="nRV" ${typingOn}></textarea></div></div>
 ${rules.length?`<div class="small muted" style="margin-top:6px">กฎของซีรีส์ที่ใช้อัตโนมัติ: ${rules.map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div>`:''}
 <h3>AI ของตอนนี้</h3><div class="grid g2">${modelPicker('nmW',roleModel('writer'),'นักเขียนบท')}${modelPicker('nmA',roleModel('animator'),'นักวาด')}</div>
 <h3>เสียงพากย์</h3>${voicePicker('nv',ser.voice)}
 <div class="row" style="margin-top:14px"><button class="primary" onclick="newEp()">เริ่มเขียนบท</button><span class="muted small">≈ $0.8 · 5–10 นาที</span></div></div>`}
async function newEp(){const lines=id=>$(id).value.split('\n').map(x=>x.trim()).filter(Boolean);
 const body={series:S.series,ep:$('nEp').value,hook:$('nHook').value,idea:$('nIdea').value.trim(),target_seconds:$('nLen').value,rules:{story:lines('nRS'),visual:lines('nRV')},voice:readVoice('nv'),models:{writer:$('nmW').value,animator:$('nmA').value}};
 if(!confirm(`เริ่มเขียนบท ${body.series} EP.${body.ep} ?`))return;
 try{await post('/api/new',body);toast('นักเขียนบทเริ่มทำงานแล้ว');S.typing=false;setTimeout(async()=>{await loadCommon();const sid=(S.seriesList.find(x=>x.slug===S.series)||{}).id;const e=S.eps.find(x=>x.series_id===sid&&String(x.number)===String(body.ep));go('produce',{ep:e?e.episode_id:null,planEp:null})},4000)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
function flowState(d){const j=d.jobs[0]||{};const by={};d.steps.filter(s=>!j.id||s.job_id===j.id).forEach(s=>by[s.role]=s);
 const st=r=>by[r]?by[r].status:(j.id?'queued':'done');const done=r=>st(r)==='done';const any=(rs,x)=>rs.some(r=>st(r)===x);
 const mk=(k,t,rs,gate)=>{let s='';if(rs.every(done))s=(gate&&j.status==='waiting_gate'&&j.gate===gate)?'now':'done';else if(any(rs,'failed'))s='fail';else if(any(rs,'running'))s='now';return{k,t,state:s}};
 const stages=[mk('script','บท',['writer'],'gate1_script'),mk('voice','เสียงพากย์',['sound','director']),mk('visual','ภาพ',['animator']),mk('final','คลิปสุดท้าย',['editor','qa'],'gate2_final'),{k:'publish',t:'โพสต์',state:j.status==='done'||!j.id?'done':''}];
 const cur=(stages.find(s=>s.state==='now'||s.state==='fail')||[...stages].reverse().find(s=>s.state==='done')||stages[0]).k;return{stages,cur,job:j,by}}
function epView(d,f,sc,ser){const e=d.episode,fs=flowState(d),j=fs.job,stage=S.stage||fs.cur;const busy=j.status==='running'||j.status==='queued';
 const fstep=d.steps.find(s=>s.job_id===j.id&&s.status==='failed');
 let h=`<div class="panel"><div class="row"><h2 class="grow" style="margin:0">${esc(e.title)}</h2><span class="cost">${money(e.cost_usd)}</span></div><div class="muted small">${epName(e)} · ยาว ${f.duration?Number(f.duration).toFixed(1)+' วิ':'—'} · เสียง ${voiceLabel(f.voice)}${busy?` · <span class="run"><span class="spin"></span>กำลัง${STEP_TH[j.current_step]||'ทำงาน'}</span>`:''}</div>
 <div class="steps" style="margin-top:12px">${fs.stages.map((s,i)=>`<div class="stp click ${s.state} ${s.k===stage?'sel':''}" onclick="S.stage='${s.k}';S.key='';render(true)"><b>${i+1}. ${s.t}</b></div>`).join('')}</div>`;
 if(j.status==='failed')h+=`<div class="gatebox" style="border-color:var(--red)"><b class="bad">ล้มที่ขั้น${esc(STEP_TH[(fstep||{}).role]||'')}</b><div class="err">${esc((j.error||'').slice(-500))}</div><div class="row" style="margin-top:8px"><button class="primary" onclick="act(${j.id},'resume')">ลองต่อจากขั้นที่ล้ม</button><button class="danger" onclick="act(${j.id},'cancel')">ยกเลิกตอนนี้</button></div></div>`;
 h+=`<div class="card">${stageView(stage,d,f,fs,sc)}</div></div>`;return h}
function stageView(k,d,f,fs,sc){const j=fs.job;const busy=j.status==='running'||j.status==='queued';
 if(k==='script'){if(!sc)return `<div class="muted">${busy?'<span class="spin"></span>นักเขียนบทกำลังเขียน… (5–10 นาที)':'ยังไม่มีบท'}</div>`;const gate=j.status==='waiting_gate'&&j.gate==='gate1_script';
  return `<div class="muted small">แก่นเรื่อง: ${esc(sc.logline||'')}</div><div class="muted small">ชื่อที่เสนอ: ${(sc.title_options||[]).map(esc).join(' · ')}</div>
  <h3>${sc.lines.length} บรรทัด · ${esc(HOOK_TH[sc.hook_type]||sc.hook_type)} · เป้า ${sc.target_seconds||'—'} วิ</h3>${sc.lines.map(l=>`<div class="line"><span class="muted">${esc(l.id)}</span><span class="muted">${esc(l.beat)}</span><span>${esc(l.text)}${l.on_screen?` <span class="os">[${esc(l.on_screen)}]</span>`:''}</span></div>`).join('')}${gate?gateBox(j,'gate1'):''}`}
 if(k==='voice')return `<div>เสียงตอนนี้: ${voiceLabel(f.voice)}</div>${f.mix?`<h3>ฟังเสียงที่ผสมแล้ว</h3><audio controls preload="none" src="${media(f.mix)}"></audio>`:''}
  <h3>เปลี่ยนเสียง</h3><div class="help">ถ้าทำภาพเสร็จแล้ว ระบบจะทำเสียงใหม่ จัดเวลาภาพ+ซับใหม่ render ใหม่เอง (ใช้ภาพเดิม ไม่เสียค่า Claude ถ้าผ่านการตรวจ)</div>${voicePicker('vp',f.voice)}
  <div class="row" style="margin-top:10px"><button class="primary" ${busy?'disabled':''} onclick="applyVoice(${j.id})">ใช้เสียงนี้กับตอนนี้</button></div>
  ${f.lines.length?`<h3>เวลาแต่ละบรรทัด · ซับคาราโอเกะ ${f.lines.filter(l=>l.words).length}/${f.lines.length}</h3>${f.lines.map(l=>`<div class="line"><span class="muted">${esc(l.id)}</span><span class="muted">${l.start.toFixed(2)} วิ</span><span>${esc(l.text)}</span></div>`).join('')}`:''}`;
 if(k==='visual'){const a=fs.by.animator||{};return `<div>สถานะ: <b>${esc(a.status||'—')}</b> · รอบที่ ${a.attempt||0} · <span class="cost">${money(a.cost_usd)}</span></div>${a.error?`<div class="err">${esc(a.error.slice(-400))}</div>`:''}${f.web?`<h3>ตัวอย่าง</h3><video controls playsinline preload="metadata" src="${media(f.web)}"></video>`:''}`}
 if(k==='final'){const gate=j.status==='waiting_gate'&&j.gate==='gate2_final';const qa=f.qa;
  return `${f.web?`<video controls playsinline preload="metadata" src="${media(f.web)}"></video>`:'<div class="muted">ยังไม่มีไฟล์</div>'}${qa?`<h3>ผลตรวจ QA</h3><div>${qa.passed?'<b class="ok">ผ่าน</b>':'<b class="bad">ไม่ผ่าน</b>'} · ${qa.duration} วิ · ${qa.lufs} LUFS</div>`:''}
  ${f.final?`<div class="small" style="margin-top:8px"><a href="${media(f.final)}" target="_blank">ดาวน์โหลด final.mp4 (1080×1920)</a></div>`:''}${gate?gateBox(j,'gate2'):''}`}
 return publishStage(S.ep,j,f)}
function gateBox(j,g){return `<div class="gatebox"><b>${g==='gate1'?'อนุมัติบทนี้ไหม?':'อนุมัติคลิปนี้ไหม?'}</b><textarea id="reason${j.id}" placeholder="${g==='gate1'?'ถ้าไม่อนุมัติ: บอกสิ่งที่อยากให้แก้ในบท':'ถ้าไม่อนุมัติ: บอกสิ่งที่อยากให้แก้ในภาพ'}" ${typingOn}></textarea>
 <div class="row" style="margin-top:8px"><button class="good" onclick="act(${j.id},'approve')">อนุมัติ</button><button class="danger" onclick="act(${j.id},'reject')">ไม่อนุมัติ + ให้แก้</button><span class="muted small">${g==='gate1'?'แก้บท ≈ $0.8':'แก้ภาพ ≈ $1'}</span></div></div>`}
async function act(job,action){let body={};if(action==='reject'){const el=$('reason'+job);body.reason=el?el.value.trim():'';if(!body.reason)return toast('ใส่เหตุผลก่อนว่าอยากให้แก้อะไร')}
 if(action==='approve'&&!confirm('อนุมัติ?'))return;if(action==='cancel'&&!confirm('ยกเลิกถาวร?'))return;
 try{await post(`/api/job/${job}/${action}`,body);toast('ส่งคำสั่งแล้ว');S.typing=false;S.stage=null;S.key='';setTimeout(()=>refresh(true),1500)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function applyVoice(job){const v=readVoice('vp');if(!confirm(`เปลี่ยนเป็น ${v.voice} ×${v.speed} ?`))return;try{await post(`/api/job/${job}/voice`,{voice:v});toast('เริ่มทำเสียงใหม่');S.key='';setTimeout(()=>refresh(true),2000)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
function voiceLabel(v){v=v||{engine:'edge',voice:'th-TH-PremwadeeNeural',speed:1};return `<span class="chip">${v.engine==='wayu'?'Wayu':'Edge'} · ${esc(v.voice)} · ×${v.speed}</span>`}
function voicePicker(id,cur){const E=(S.voices||{}).engines||{edge:['th-TH-PremwadeeNeural','th-TH-NiwatNeural']};cur=cur||{engine:'edge',voice:'th-TH-PremwadeeNeural',speed:1};
 const opts=Object.entries(E).flatMap(([en,vs])=>vs.map(v=>`<option value="${en}|${v}" ${cur.engine===en&&cur.voice===v?'selected':''}>${en==='wayu'?'Wayu (ในเครื่อง)':'Edge (ออนไลน์)'} · ${v}</option>`)).join('');
 const samples=((S.voices||{}).samples||[]).map(p=>`<div class="row small"><span class="grow muted">${esc(p.split('/').pop())}</span><audio controls preload="none" src="${media(p)}" style="max-width:260px"></audio></div>`).join('');
 return `<div class="grid g2"><div><label>เสียง</label><select id="${id}v" style="width:100%">${opts}</select></div><div><label>ความเร็ว (เท่า)</label><input type="number" id="${id}s" min="0.8" max="1.6" step="0.05" value="${cur.speed||1}"></div></div>
 <div class="small wait" style="margin-top:6px">⚠ Wayu ห้ามใช้เชิงพาณิชย์ (CC-BY-NC) · Edge (edge-tts) ไม่มีใบอนุญาตเชิงพาณิชย์ที่ชัดเจน — ถ้าจะหาเงินให้ใช้ Azure Speech แบบจ่ายเงินหรือโมเดลที่เป็น MIT/Apache · Edge บางครั้งล่ม</div>${samples?`<details><summary class="muted small">ตัวอย่างเสียง</summary>${samples}</details>`:''}`}
function readVoice(id){const [engine,voice]=$(id+'v').value.split('|');return {engine,voice,speed:parseFloat($(id+'s').value||'1')}}

// ================= ④ Agents =================
function agentsView(){const ag=S.agents;if(!ag)return '';const key=JSON.stringify(['agents',ag]);if(key===S.key)return null;S.key=key;
 const st={working:'กำลังทำงาน',needs_you:'รอคุณ',error:'มีปัญหา',waiting:'รอขั้นก่อนหน้า',idle:'ว่าง'};
 const needs=ag.agents.flatMap(a=>a.items.filter(i=>i.kind==='need').map(i=>({...i,who:a.name})));
 return `<div class="panel"><div class="row"><h2 class="grow" style="margin:0">Agents — ใครทำอะไรอยู่ รออะไร ต้องการอะไร</h2><span class="muted small">อัปเดต ${esc(ag.time)} (ทุก 5 วิ)</span></div>
 ${needs.length?`<div class="gatebox"><b>รอคุณตัดสิน ${needs.length} เรื่อง</b>${needs.map(n=>`<div class="row small" style="margin-top:6px"><span class="grow">• ${esc(n.who)}: ${esc(n.text)}</span>${n.plan?`<button onclick="go('${n.text.includes('bible')?'series':'story'}',{plan:${n.plan}})">ไปดู</button>`:''}${n.job?`<button onclick="openJob(${n.job})">ไปดู</button>`:''}</div>`).join('')}</div>`:'<div class="help">ตอนนี้ไม่มีอะไรรอคุณ</div>'}
 <div class="help small" style="margin-top:12px">ลำดับการทำงาน: วางเรื่อง → เขียนบท → เสียง → แบ่งช็อต → วาด → ตัดต่อ → ตรวจ (agent ที่ใช้ Claude เสียโควตา ที่เหลือฟรี)</div><div class="grid g2" style="margin-top:8px">${ag.agents.map((a,i)=>`<div class="agent ${a.state}"><div class="row"><b class="grow"><span class="dot"></span>${i+1}. ${esc(a.name)}</b><span class="chip">${esc(a.engine)}</span><span class="status">${st[a.state]||a.state}</span></div>
  <div class="small muted">${esc(a.does)}</div>
  ${a.doing?`<div class="small run" style="margin-top:8px"><span class="spin"></span>${esc(a.doing)}</div>`:''}
  ${a.waiting_for?`<div class="small wait" style="margin-top:6px">⏸ ${esc(a.waiting_for)}</div>`:''}
  ${a.activity.length?`<div class="act">${a.activity.map(esc).join('\n')}</div>`:''}
  ${a.items.map(i=>`<div class="small ${i.kind==='need'?'wait':i.kind==='error'?'bad':'muted'}" style="margin-top:6px">${i.kind==='need'?'🔔':i.kind==='error'?'⚠':'⏳'} ${esc(i.text)}</div>`).join('')}</div>`).join('')}</div></div>`}
function openJob(jid){const j=S.jobs.find(x=>x.id===jid);if(!j)return;const ep=S.eps.find(e=>e.episode_id===j.episode_id);go('produce',{series:j.series,ep:j.episode_id,stage:null})}

// ================= ⑤ ตั้งค่า =================
async function settingsView(){const key=JSON.stringify(['settings',S.series]);if(key===S.key)return null;S.key=key;const s=await api('/api/series/'+encodeURIComponent(S.series||'x'));
 const list=(k,items)=>`<div id="rl_${k}">${(items.length?items:['']).map(x=>ruleRow(k,x)).join('')}</div><button onclick="addRule('${k}')">+ เพิ่มกฎ</button>`;
 return `<div class="panel"><div class="row"><h2 class="grow" style="margin:0">ตั้งค่าซีรีส์</h2><select onchange="S.series=this.value;S.key='';render(true)">${S.seriesList.map(x=>`<option ${x.slug===S.series?'selected':''}>${esc(x.slug)}</option>`).join('')}</select></div>
 <div class="help" style="margin-top:10px">กฎที่นี่ใช้กับทุกตอนของซีรีส์ (ถ้าซีรีส์มาจาก bible กฎจะถูกเติมให้แล้ว แก้ต่อได้)</div>
 <div class="grid g2">${fld('sTitle2','ชื่อซีรีส์',s.title)}${fld('sGenre2','แนว',s.genre)}</div>
 <h3>กฎเนื้อเรื่อง (ใช้ตอนเขียนบท)</h3>${list('story',s.rules.story||[])}<h3>กฎภาพ (ใช้ตอนวาด)</h3>${list('visual',s.rules.visual||[])}
 <h3>เสียงเริ่มต้นของซีรีส์</h3>${voicePicker('sv',s.voice)}<div class="row" style="margin-top:14px"><button class="primary" onclick="saveRules()">บันทึก</button></div></div>
 <div class="panel"><h2>AI เริ่มต้นของแต่ละหน้าที่ (ทุกซีรีส์)</h2><div class="help">ค่าเริ่มต้นตอนสร้างแผน/ตอนใหม่ — เปลี่ยนเฉพาะงานได้ในฟอร์มของงานนั้น · Opus เก่งสุดแต่กินโควตามากสุด (~2× Sonnet) และติด 429 เร็วกว่า</div>
 <div class="grid g3">${Object.entries((S.models||{}).roles||{}).map(([r,x])=>modelPicker('dm_'+r,x.model,x.label)).join('')}</div>
 <div class="row" style="margin-top:12px"><button class="primary" onclick="saveModels()">บันทึก AI เริ่มต้น</button></div></div>`}
async function saveModels(){const roles={};Object.keys((S.models||{}).roles||{}).forEach(r=>roles[r]=$('dm_'+r).value);try{await post('/api/models',{roles});S.models=await api('/api/models');toast('บันทึกแล้ว');S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
function ruleRow(k,x){return `<div class="row" style="margin:5px 0"><input type="text" class="r_${k} grow" value="${esc(x)}" style="width:auto;flex:1" ${typingOn}><button onclick="this.parentNode.remove()">ลบ</button></div>`}
function addRule(k){$('rl_'+k).insertAdjacentHTML('beforeend',ruleRow(k,''))}
const rulesFrom=k=>[...document.querySelectorAll('.r_'+k)].map(i=>i.value.trim()).filter(Boolean);
async function saveRules(){try{await post('/api/series/'+S.series,{title:$('sTitle2').value,genre:$('sGenre2').value,rules:{story:rulesFrom('story'),visual:rulesFrom('visual')},voice:readVoice('sv')});toast('บันทึกแล้ว');S.key='';S.typing=false;render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}

// ================= boot =================
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>go(t.dataset.t));
Promise.all([api('/api/voices').then(v=>S.voices=v).catch(()=>{}),api('/api/models').then(m=>S.models=m).catch(()=>{S.models={choices:[],roles:{}}})]).finally(()=>refresh(true));setInterval(()=>refresh(false),5000);

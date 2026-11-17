// ================= Edit any stage: script / voice / picture (also reopens cancelled or failed episodes) =================
const BEAT_TH={hook:'เปิดเรื่อง',setup:'ปูเรื่อง',escalate:'เร่ง',twist:'หักมุม',cliff:'ค้างไว้'};
const _stageView=stageView, _epView=epView;

function jobBusy(j){return j.status==='running'||j.status==='queued'}

epView=function(d,f,sc,ser){let h=_epView(d,f,sc,ser);const j=(d.jobs||[])[0]||{};
 if(j.status==='cancelled'){const box=`<div class="gatebox" style="border-color:var(--muted)"><b>ตอนนี้ถูกยกเลิกไว้</b><div class="small">ไม่ต้องสร้างตอนใหม่: กดขั้น 1. บท / 2. เสียงพากย์ / 3. ภาพ ด้านบน แล้วใช้กล่องสีเหลืองในขั้นนั้นแก้ ระบบจะทำต่อจากขั้นนั้นให้ ไฟล์เดิมยังอยู่ครบ</div></div>`;
  h=h.replace('<div class="card">',box+'<div class="card">')}
 return h};

stageView=function(k,d,f,fs,sc){const base=_stageView(k,d,f,fs,sc);const j=fs.job;if(!j.id)return base;
 const busy=j.status==='running';const by=fs.by||{};
 const dis=busy?'disabled':'';const note=busy?'<div class="small wait">agent กำลังทำงาน — แก้ได้เมื่อเสร็จ</div>':'';
 if(k==='script'){if(!sc||(by.writer||{}).status!=='done')return base;
  const after=(by.sound||{}).attempt>0;
  return base+`<div class="gatebox" style="margin-top:14px"><b>แก้บทตอนนี้</b>${note}
  <div class="small muted">${after?'แก้แล้วระบบจะทำเสียง+เวลาซับใหม่ให้เอง · ถ้าจำนวน/ลำดับบรรทัดเท่าเดิมจะใช้ภาพเดิม (ฟรี) · ถ้าเพิ่ม/ลบบรรทัดต้องวาดภาพใหม่ (≈ $1.2)':'แก้แล้วยังรอคุณอนุมัติบทเหมือนเดิม'}</div>
  <div class="row" style="margin-top:8px"><button class="primary" ${dis} onclick="openScriptEditor()">✎ แก้บทเอง</button></div>
  <textarea id="rwReason" placeholder="หรือบอก AI ว่าอยากให้เขียนใหม่ยังไง เช่น ให้ตอนจบหักมุมแรงกว่านี้" ${typingOn}></textarea>
  <div class="row" style="margin-top:6px"><button ${dis} onclick="redo(${j.id},'writer')">✨ ให้ AI เขียนบทใหม่</button><span class="muted small">≈ $0.3–0.8 · กลับไปรออนุมัติบท · เสียง/ภาพเดิมจะถูกทำใหม่</span></div></div>
  ${S.editScript&&S.editScript.job===j.id?scriptEditor():''}`}
 if(k==='voice'){if((by.writer||{}).status!=='done')return base;
  return base+`<div class="gatebox" style="margin-top:14px"><b>ทำเสียงพากย์ใหม่</b>${note}
  <div class="small muted">เลือกช่วงที่จะให้พากย์ใหม่ (เสียงเดิมเก็บไว้ใน backup) · ภาพเดิมจะถูกจัดเวลาใหม่ตามเสียง ไม่เสียค่า AI ถ้าผ่านการตรวจ</div>
  <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:6px">${Object.entries(BEAT_TH).map(([b,t])=>`<label class="small"><input type="checkbox" class="rvBeat" value="${b}"> ${t}</label>`).join('')}</div>
  <div class="row" style="margin-top:8px"><button ${dis} onclick="redoVoice(${j.id},'pick')">พากย์ใหม่เฉพาะที่เลือก</button><button ${dis} onclick="redoVoice(${j.id},'all')">พากย์ใหม่ทั้งตอน</button><button ${dis} onclick="redoVoice(${j.id},'retime')">ใช้เสียงเดิม จัดเวลาใหม่</button></div></div>`}
 if(k==='visual'){if((by.sound||{}).status!=='done')return base;const has=!!((by.animator||{}).attempt);
  return base+`<div class="gatebox" style="margin-top:14px"><b>แก้ภาพ</b>${note}
  <textarea id="rvReason" rows="4" placeholder="บอกสิ่งที่อยากให้แก้ เช่น ช็อต 5 มืดไป, ตัวละครลอย, ซับบังหน้า" ${typingOn}></textarea>
  <div class="row" style="margin-top:6px">${has?`<button class="primary" ${dis} onclick="redo(${j.id},'animator')">✨ ให้ AI แก้ตามนี้</button><span class="muted small">≈ $0.5–1 · 10–15 นาที</span></div>
  <div class="row" style="margin-top:14px"><button ${dis} onclick="redo(${j.id},'animator',true)">วาดใหม่ทั้งหมด</button><span class="muted small">ทิ้งภาพเดิม (เก็บไว้ใน backup) ≈ $1.2 · 15–20 นาที</span></div></div>`:`<button class="primary" ${dis} onclick="redo(${j.id},'animator',true)">เริ่มวาดภาพ</button><span class="muted small">≈ $1.2 · 15–20 นาที</span></div></div>`}`}
 return base};

async function redo(job,from,fresh){const reason=(from==='writer'?$('rwReason'):from==='animator'?$('rvReason'):null);const r=reason?reason.value.trim():'';
 if(from==='animator'&&!fresh&&!r)return toast('บอกก่อนว่าอยากให้แก้ภาพตรงไหน');
 const what=from==='writer'?'ให้ AI เขียนบทใหม่':fresh?'วาดภาพใหม่ทั้งหมด':'ให้ AI แก้ภาพ';if(!confirm(what+' ?'))return;
 try{await post(`/api/job/${job}/redo`,{from,reason:r,fresh:!!fresh});toast('ส่งงานแล้ว');S.typing=false;S.key='';setTimeout(()=>refresh(true),2000)}catch(e){toast('ผิดพลาด: '+e.message,9000)}}
async function redoVoice(job,mode){let beats=null;
 if(mode==='pick'){beats=[...document.querySelectorAll('.rvBeat:checked')].map(x=>x.value);if(!beats.length)return toast('เลือกช่วงก่อน')}
 if(mode==='retime')beats=[];
 const msg=mode==='all'?'พากย์ใหม่ทั้งตอน':mode==='retime'?'ใช้เสียงเดิม จัดเวลา/ซับใหม่':'พากย์ใหม่: '+beats.map(b=>BEAT_TH[b]).join(', ');if(!confirm(msg+' ?'))return;
 try{await post(`/api/job/${job}/redo`,{from:'sound',beats});toast('เริ่มทำเสียงใหม่');S.key='';setTimeout(()=>refresh(true),2000)}catch(e){toast('ผิดพลาด: '+e.message,9000)}}

// ---- hand editor for the script ----
async function openScriptEditor(){const sc=await api(`/api/episode/${S.ep}/script`);const j=jobOf(S.ep)||{};
 S.editScript={job:j.id,sc:JSON.parse(JSON.stringify(sc))};S.key='';await render(true);S.typing=true;const el=$('scriptEditor');el&&el.scrollIntoView({block:'start'})}
function scriptEditor(){const sc=S.editScript.sc;
 const row=(l,i)=>`<div class="card" style="margin:6px 0"><div class="row"><span class="muted small grow">${esc(l.id)}</span>
  <select onchange="seSet(${i},'beat',this.value)">${Object.entries(BEAT_TH).map(([b,t])=>`<option value="${b}" ${l.beat===b?'selected':''}>${t}</option>`).join('')}</select>
  <button class="small" onclick="seMove(${i},-1)">↑</button><button class="small" onclick="seMove(${i},1)">↓</button><button class="small danger" style="margin-left:10px" onclick="seDel(${i})">ลบ</button></div>
  <textarea rows="2" oninput="seSet(${i},'text',this.value)" ${typingOn}>${esc(l.text)}</textarea>
  <div class="row small" style="margin-top:4px;gap:6px"><span class="muted">บนจอ:</span><input type="text" class="grow" placeholder="(ไม่บังคับ)" value="${esc(l.on_screen||'')}" oninput="seSet(${i},'on_screen',this.value)" ${typingOn}></div>
  <button class="small" style="margin-top:4px" onclick="seAdd(${i})">+ แทรกบรรทัดหลังนี้</button></div>`;
 return `<div class="panel" id="scriptEditor" style="margin-top:12px"><h3>✎ ตัวแก้บท</h3><div class="small muted">บรรทัดแรก = เสียงอ่านชื่อเรื่อง ต้องสั้น (อ่านจบใน 2.8 วิ) · 15–21 บรรทัด · ห้ามคำอังกฤษยาวในเสียงพากย์ (ใส่ในข้อความบนจอแทน)</div>
 <label>ชื่อตอน (ต้องมี EP.${sc.episode})</label><input type="text" value="${esc(sc.title)}" oninput="S.editScript.sc.title=this.value" ${typingOn}>
 ${sc.lines.map(row).join('')}
 <div class="row" style="margin-top:10px;position:sticky;bottom:0;background:var(--panel,#121722);padding:10px 0;z-index:2"><button class="primary" onclick="saveScript()">บันทึกบท</button><button onclick="S.editScript=null;S.typing=false;S.key='';render(true)">ยกเลิก</button><span class="muted small">${sc.lines.length} บรรทัด</span></div></div>`}
function seSet(i,k,v){S.editScript.sc.lines[i][k]=v}
function seMove(i,dz){const L=S.editScript.sc.lines,k=i+dz;if(k<0||k>=L.length)return;[L[i],L[k]]=[L[k],L[i]];S.key='';render(true)}
function seDel(i){const L=S.editScript.sc.lines;if(L.length<=15)return toast('ต้องมีอย่างน้อย 15 บรรทัด');if(!confirm('ลบบรรทัด '+L[i].id+' ?'))return;L.splice(i,1);S.key='';render(true)}
function seAdd(i){const L=S.editScript.sc.lines;if(L.length>=21)return toast('ได้ไม่เกิน 21 บรรทัด');
 let n=1;while(L.some(l=>l.id==='l'+String(n).padStart(2,'0')+'x'))n++;L.splice(i+1,0,{id:'l'+String(i+2).padStart(2,'0')+'x'+n,beat:L[i].beat,text:'',on_screen:''});S.key='';render(true)}
async function saveScript(){const sc=S.editScript.sc;if(sc.lines.some(l=>!l.text.trim()))return toast('มีบรรทัดว่าง');
 if(!confirm('บันทึกบท? (ถ้ามีเสียง/ภาพแล้ว ระบบจะทำส่วนที่เกี่ยวข้องใหม่ให้)'))return;
 try{const d=await post(`/api/job/${S.editScript.job}/script`,{script:sc});S.editScript=null;S.typing=false;S.key='';
  toast(d.rerun?(d.rerun==='sound'?'บันทึกแล้ว · กำลังทำเสียงช่วง '+(d.changed_beats||[]).map(b=>BEAT_TH[b]).join(', ')+' ใหม่ (ใช้ภาพเดิม)':'บันทึกแล้ว · บรรทัดเปลี่ยน ต้องทำเสียงและวาดภาพใหม่'):'บันทึกแล้ว · รออนุมัติบท',9000);setTimeout(()=>refresh(true),1500)}
 catch(e){toast('บันทึกไม่ได้: '+e.message,10000)}}

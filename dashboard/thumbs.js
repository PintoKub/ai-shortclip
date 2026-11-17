// ================= Thumbnail / clickbait cover stage (between "final clip" and "post") =================
// AI looks at clean frames of the episode and proposes cover ideas; the user edits text/colour/frame,
// renders as many covers as they like (free), and picks one. Posting sends it to YouTube (custom
// thumbnail), TikTok (cover frame) and Instagram (thumb_offset).
const TPL_TH={top:'ข้อความบน',bottom:'ข้อความล่าง',center:'กลางจอ + กรอบ',split:'เฉียง'};
const COL_TH={yellow:'เหลือง',white:'ขาว',red:'แดง',cyan:'ฟ้า',green:'เขียว'};
S.th={eid:null,data:null,spec:null,busy:false};

const _flowState2=flowState;
flowState=function(d){const fs=_flowState2(d);const j=fs.job;const i=fs.stages.findIndex(s=>s.k==='publish');
 const finalOk=fs.stages.find(s=>s.k==='final');const st=finalOk&&finalOk.state==='done'?(S.th.data&&S.th.eid===S.ep&&S.th.data.selected?'done':''):'';
 if(i>=0&&!fs.stages.find(s=>s.k==='thumb'))fs.stages.splice(i,0,{k:'thumb',t:'ปก',state:st});return fs};

const _stageView3=stageView;
stageView=function(k,d,f,fs,sc){if(k!=='thumb')return _stageView3(k,d,f,fs,sc);return thumbStage(S.ep,fs,f)};

async function thLoad(eid){S.th.data=await api(`/api/episode/${eid}/thumb`);S.th.eid=eid}
function thumbStage(eid,fs,f){
 const by=fs.by||{};if((by.animator||{}).status!=='done'&&!f.final)return '<span class="muted">ต้องมีภาพของตอนก่อน (ขั้น 3. ภาพ)</span>';
 if(!S.th.data||S.th.eid!==eid){thLoad(eid).then(()=>{S.key='';render(true)}).catch(e=>toast(e.message));return '<span class="spin"></span>กำลังโหลด…'}
 const D=S.th.data,base=D.base;const img=n=>media(base+'/'+n);
 if(D.busy&&!S.th.poll){S.th.poll=setTimeout(async()=>{S.th.poll=null;await thLoad(eid);S.key='';render(true)},6000)}
 const frames=D.frames||[];
 if(!frames.length)return `<div class="help">ยังไม่มีภาพนิ่งของตอนนี้</div><button class="primary" onclick="thFrames(${eid})">ดึงภาพจากคลิป (ฟรี ~5 วิ)</button>`;
 const sp=S.th.spec||(D.ideas[0]?{...D.ideas[0]}:{frame:frames[Math.min(2,frames.length-1)].file,headline:'',sub:'',template:'top',color:'yellow',zoom:1,focus_y:50,badge:'',emoji:'',arrow:false});S.th.spec=sp;
 const ideas=D.ideas.length?`<h3>ไอเดียจาก AI <span class="muted small">กด "ใช้ไอเดียนี้" แล้วแก้ต่อในฟอร์มด้านล่าง</span></h3><div class="row" style="flex-wrap:wrap;gap:8px">${D.ideas.map((x,i)=>`<div class="card" style="width:170px;padding:6px"><img src="${img('frames/'+x.frame)}" style="width:100%;border-radius:6px"><b class="small">${esc(x.headline)}</b><div class="muted small">${esc(x.sub||'')}</div><div class="small" style="color:var(--muted)">${esc(x.why||'')}</div><button class="small primary" style="width:100%;margin-top:4px" onclick="thUse(${i})">ใช้ไอเดียนี้ ↓</button></div>`).join('')}</div>`:'';
 const covers=(D.covers||[]).length?`<h3>ปกที่ทำแล้ว <span class="muted small">กด "ใช้ปกนี้" เพื่อเลือกตัวที่จะโพสต์</span></h3><div class="row" style="flex-wrap:wrap;gap:10px">${D.covers.slice().reverse().map(c=>{const on=D.selected&&D.selected.file===c.file;return `<div class="card" style="width:180px;padding:6px;${on?'border-color:var(--green);box-shadow:0 0 0 2px var(--green)':''}"><a href="${img(c.file)}" target="_blank"><img src="${img(c.file)}" style="width:100%;border-radius:6px"></a>
  <div class="row small" style="gap:4px;margin-top:4px">${on?'<b class="ok grow">✓ ใช้ปกนี้</b>':`<button class="good small grow" onclick="thSelect(${eid},'${c.file}')">ใช้ปกนี้</button>`}<button class="small" title="แก้ต่อจากปกนี้" onclick="thEdit('${c.file}')">✎</button><button class="small danger" onclick="thDel(${eid},'${c.file}')">ลบ</button></div></div>`}).join('')}</div>`:'';
 const fpick=`<div class="row" style="flex-wrap:nowrap;overflow-x:auto;gap:6px;padding-bottom:6px">${frames.map(x=>`<img src="${img('frames/'+x.file)}" title="${esc(x.text)}" onclick="thSet('frame','${x.file}');S.key='';render(true)" style="height:150px;border-radius:6px;cursor:pointer;border:3px solid ${x.file===sp.frame?'var(--yellow)':'transparent'}">`).join('')}</div>`;
 const opt=(o,cur)=>Object.entries(o).map(([k,v])=>`<option value="${k}" ${k===cur?'selected':''}>${v}</option>`).join('');
 return `<div class="help">ปกคือสิ่งแรกที่คนเห็นก่อนกดดู · AI ช่วยคิดข้อความได้ (≈ $0.1–0.3) · ทำปกเองได้ไม่จำกัด (ฟรี) · ปกที่เลือกจะถูกส่งตอนโพสต์: YouTube = ภาพปก, TikTok/IG = ใช้เฟรมเดียวกันเป็นปก</div>
 <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">${D.busy?'<span class="run"><span class="spin"></span>AI กำลังดูภาพและคิดปก… (1–2 นาที)</span>':`<button class="primary" onclick="thIdeas(${eid})">✨ ให้ AI คิดปก clickbait</button><input type="text" id="thExtra" placeholder="อยากได้แนวไหน (ไม่บังคับ) เช่น เน้นนโปเลียนโกรธ" style="min-width:260px" ${typingOn}>`}</div>
 ${ideas}
 <h3 id="thForm">ทำปก</h3><label>ภาพพื้นหลัง (เลื่อนดูได้ · ไม่มีซับ · ถ้าภาพมีกล่องคำพูดอยู่ตรงที่จะวางข้อความ ให้เปลี่ยนตำแหน่งข้อความ)</label>${fpick}
 <div class="grid g2" style="align-items:start;margin-top:8px"><div>
  <label>ข้อความหลัก (สั้น ๆ ≤ 18 ตัวดีสุด)</label><input type="text" id="thHead" maxlength="40" value="${esc(sp.headline||'')}" oninput="thSet('headline',this.value)" ${typingOn}>
  <label style="margin-top:6px">บรรทัดรอง (แถบแดง ไม่บังคับ)</label><input type="text" maxlength="60" value="${esc(sp.sub||'')}" oninput="thSet('sub',this.value)" ${typingOn}>
  <div class="grid g3" style="margin-top:6px"><div><label>ตำแหน่ง</label><select onchange="thSet('template',this.value)" style="width:100%">${opt(TPL_TH,sp.template)}</select></div>
   <div><label>สีตัวอักษร</label><select onchange="thSet('color',this.value)" style="width:100%">${opt(COL_TH,sp.color)}</select></div>
   <div><label>ป้ายมุม</label><input type="text" maxlength="16" value="${esc(sp.badge||'')}" placeholder="เช่น EP.5" oninput="thSet('badge',this.value)" ${typingOn}></div></div>
  <div class="grid g3" style="margin-top:6px"><div><label>อีโมจิ</label><input type="text" maxlength="4" value="${esc(sp.emoji||'')}" placeholder="😱" oninput="thSet('emoji',this.value)" ${typingOn}></div>
   <div><label>ซูมภาพ ${Number(sp.zoom||1).toFixed(2)}×</label><input type="range" min="1" max="1.6" step="0.05" value="${sp.zoom||1}" oninput="thSet('zoom',this.value)" onchange="S.key='';render(true)"></div>
   <div><label>จุดโฟกัส บน↕ล่าง ${sp.focus_y??50}%</label><input type="range" min="0" max="100" step="5" value="${sp.focus_y??50}" oninput="thSet('focus_y',this.value)" onchange="S.key='';render(true)"></div></div>
  <label class="row small" style="margin-top:6px"><input type="checkbox" ${sp.arrow?'checked':''} onchange="thSet('arrow',this.checked)"> ลูกศรแดง</label>
  <div class="row" style="margin-top:10px"><button class="primary" ${S.th.busy?'disabled':''} onclick="thRender(${eid})">${S.th.busy?'<span class="spin"></span>กำลังทำ…':'สร้างปกนี้'}</button><span class="muted small">ฟรี · ~2 วิ</span></div>
 </div><div><div class="small muted">ภาพที่ใช้</div><img src="${img('frames/'+sp.frame)}" style="width:100%;max-width:300px;border-radius:8px"></div></div>
 ${covers}
 ${D.selected?`<div class="row" style="margin-top:12px"><b class="ok grow">เลือกปกแล้ว: ${esc(D.selected.file)}</b><button class="primary" onclick="S.stage='publish';S.key='';render(true)">ไปขั้นโพสต์ →</button></div>`:''}`}
function thSet(k,v){S.th.spec=S.th.spec||{};S.th.spec[k]=(k==='zoom'||k==='focus_y')?Number(v):v}
function thUse(i){S.th.spec={...S.th.data.ideas[i]};S.key='';render(true).then(()=>{const el=$('thForm');el&&el.scrollIntoView({block:'start',behavior:'smooth'})});toast('โหลดไอเดียเข้าฟอร์มแล้ว แก้ได้ แล้วกด "สร้างปกนี้"')}
function thEdit(file){const c=(S.th.data.covers||[]).find(x=>x.file===file);if(c){S.th.spec={...c.spec};S.key='';render(true);toast('โหลดค่าของปกนี้แล้ว แก้แล้วกด "สร้างปกนี้"')}}
async function thFrames(eid){toast('กำลังดึงภาพ…');try{await post(`/api/episode/${eid}/thumb/frames`,{});await thLoad(eid);S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function thIdeas(eid){const extra=($('thExtra')||{}).value||'';try{await post(`/api/episode/${eid}/thumb/ideas`,{extra,count:5});toast('AI เริ่มคิดปกแล้ว (1–2 นาที)');await thLoad(eid);S.th.data.busy=true;S.typing=false;S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function thRender(eid){const sp=S.th.spec||{};if(!(sp.headline||'').trim())return toast('ใส่ข้อความหลักก่อน');S.th.busy=true;S.key='';render(true);
 try{await post(`/api/episode/${eid}/thumb/render`,{spec:sp});await thLoad(eid);toast('สร้างปกแล้ว — กด "ใช้ปกนี้" ถ้าชอบ')}catch(e){toast('ทำปกไม่ได้: '+e.message,9000)}S.th.busy=false;S.typing=false;S.key='';render(true)}
async function thSelect(eid,file){try{await post(`/api/episode/${eid}/thumb/select`,{file});await thLoad(eid);toast('เลือกปกแล้ว');S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function thDel(eid,file){if(!confirm('ลบปกนี้?'))return;try{await post(`/api/episode/${eid}/thumb/delete`,{file});await thLoad(eid);S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}

// show the chosen cover on the post screen
const _publishStage=publishStage;
publishStage=function(eid,j,f){let h=_publishStage(eid,j,f);
 if(!S.th.data||S.th.eid!==eid){thLoad(eid).then(()=>{S.key='';render(true)}).catch(()=>{});return h}
 const sel=S.th.data.selected;const box=sel?`<div class="row small" style="gap:8px;margin:0 0 10px"><img src="${media(S.th.data.base+'/'+sel.file)}" style="height:110px;border-radius:6px"><div>ปกที่จะส่ง: <b>${esc(sel.file)}</b><br><span class="muted">YouTube = ภาพปก (ช่องต้องยืนยันเบอร์โทรก่อน) · TikTok/IG = ใช้เฟรมวินาทีที่ ${(sel.frame_ms/1000).toFixed(1)} เป็นปก</span><br><a href="#" onclick="S.stage='thumb';S.key='';render(true);return false">เปลี่ยนปก</a></div></div>`
  :`<div class="small wait" style="margin-bottom:10px">ยังไม่ได้เลือกปก — แพลตฟอร์มจะสุ่มเฟรมเอง <a href="#" onclick="S.stage='thumb';S.key='';render(true);return false">ไปทำปก</a></div>`;
 return box+h};

// Bible editor: edit one item at a time (character / episode / lore / world / rules / basics),
// add / delete / reorder, pick lore sources from real search results, or let AI rewrite one item.
// Loaded after app.js. Overrides bibleView() from app.js.
S.be={edit:null,draft:null,sources:null,hits:null,searching:false,regenFor:null};
const BE_KIND={place:'สถานที่',legend:'ตำนาน',history:'ประวัติศาสตร์',person:'บุคคล',culture:'วัฒนธรรม'};
const lines=v=>String(v||'').split('\n').map(x=>x.trim()).filter(Boolean);
const beBusy=p=>!!p.busy_step&&String(p.busy_step).startsWith('regen')&&p.alive;

function beBtns(p,target,label){if(beBusy(p))return '';const editing=S.be.edit===target;
 return `<span class="row" style="gap:4px">${editing?'':`<button class="small" onclick="beEdit('${target}')">✎ แก้</button>`}<button class="small" onclick="S.be.regenFor=S.be.regenFor==='${target}'?null:'${target}';S.key='';render(true)" title="ให้ AI เขียนใหม่เฉพาะส่วนนี้">✨ AI</button></span>`}
function beRegenBox(p,target,what){if(S.be.regenFor!==target||beBusy(p))return '';
 return `<div class="gatebox" style="margin:6px 0"><b class="small">ให้ AI เขียน${esc(what)}ใหม่</b><textarea id="beInstr" placeholder="อยากให้เปลี่ยนอย่างไร เช่น ให้ตลกขึ้น · ให้มีความลับเกี่ยวกับตอน 3 · ใช้สถานที่จริงในลาดกระบัง" ${typingOn}></textarea>
 <div class="row" style="margin-top:6px"><button class="primary" onclick="beRegen(${p.id},'${target}')">เขียนใหม่</button><span class="muted small">≈ $0.15–0.5 · 1–4 นาที · AI ของแผนนี้ · ส่วนอื่นไม่เปลี่ยน</span><button onclick="S.be.regenFor=null;S.key='';render(true)">ยกเลิก</button></div></div>`}

function bibleView(p){const b=p.bible,w=p.warnings||[];S.be.plan=p;
 const busy=beBusy(p)?`<div class="gatebox"><span class="spin"></span>AI กำลังเขียนใหม่ — แก้ bible ได้อีกครั้งเมื่อเสร็จ${p.quota_wait?' · '+esc(p.quota_wait):''}</div>`:'';
 const err=p.error&&p.status!=='failed'?`<div class="gatebox" style="border-color:var(--red)"><div class="err small">${esc(p.error)}</div></div>`:'';
 const sec=(t,target,inner,add)=>`<div class="row" style="margin-top:18px"><h3 class="grow" style="margin:0">${t}</h3>${add&&!beBusy(p)?add:''}</div>${inner}`;
 let h=`<hr style="border-color:var(--line);margin:18px 0"><div class="row"><h2 class="grow" style="margin:0">Series bible</h2><span class="muted small">แก้ทีละส่วนได้ · บันทึกแล้ว${p.status==='approved'?'อัปเดตซีรีส์ทันที (ตอนที่ผลิตไปแล้วไม่เปลี่ยน)':'มีผลตอนอนุมัติ'}</span></div>${busy}${err}${w.map(x=>`<div class="small wait">⚠ ${esc(x)}</div>`).join('')}`;
 // basics
 h+=sec('ข้อมูลหลัก','basics',S.be.edit==='basics'?beBasicsForm(b):`<div class="row"><div class="grow small"><b>${esc(b.title)}</b> · ${esc(b.genre)} · ${esc(b.tone)} · ${esc(b.audience)}<div>${esc(b.logline)}</div></div>${beBtns(p,'basics').replace(/<button class="small" onclick="S.be.regenFor[^<]*<\/button>/,'')}</div>`);
 // world
 const wd=b.world||{},vs=b.visual_style||{};
 h+=sec('โลก + สไตล์ภาพ','world',S.be.edit==='world'?beWorldForm(b):`<div class="row" style="align-items:flex-start"><div class="grow small">${esc(wd.setting)} ${wd.era?'· '+esc(wd.era):''}${(wd.rules||[]).map(r=>`<div>• ${esc(r)}</div>`).join('')}${vs.palette?`<div class="muted">โทนสี: ${esc(vs.palette)}</div>`:''}${(vs.recurring_places||[]).length?`<div class="muted">สถานที่ประจำ: ${vs.recurring_places.map(esc).join(' · ')}</div>`:''}${vs.notes?`<div class="muted">${esc(vs.notes)}</div>`:''}</div>${beBtns(p,'world')}</div>${beRegenBox(p,'world','โลก/สไตล์ภาพ')}`);
 // characters
 h+=sec(`ตัวละคร (${b.characters.length}/8)`,'',`<div class="grid g2">${b.characters.map(c=>S.be.edit==='character:'+c.id?`<div class="card" style="grid-column:1/-1">${beCharForm(b,c)}</div>`:`<div class="card"><div class="row"><b class="grow">${esc(c.name)}</b><span class="chip">${esc(c.role)}</span>${beBtns(p,'character:'+c.id)}</div>
  <div class="small muted">${esc(c.age)} · ${esc(c.look)}</div><div class="small" style="margin-top:4px">${esc(c.personality)}</div>
  <div class="small" style="margin-top:4px"><b class="ok">ทำได้:</b> ${c.abilities.map(esc).join(' · ')}</div><div class="small"><b class="bad">ทำไม่ได้:</b> ${c.limits.map(esc).join(' · ')}</div>
  ${c.goal?`<div class="small"><b>เป้าหมาย:</b> ${esc(c.goal)}</div>`:''}${c.secret?`<div class="small"><b>ความลับ:</b> ${esc(c.secret)}</div>`:''}${c.arc?`<div class="small"><b>พัฒนาการ:</b> ${esc(c.arc)}</div>`:''}${beRegenBox(p,'character:'+c.id,' '+c.name+' ')}</div>`).join('')}
  ${S.be.edit==='character:new'?`<div class="card" style="grid-column:1/-1">${beCharForm(b,null)}</div>`:''}</div>${beRegenBox(p,'character:new','ตัวละครใหม่ 1 ตัว')}`,
  b.characters.length<8?`<span class="row" style="gap:4px"><button class="small" onclick="beEdit('character:new')">+ เพิ่มเอง</button><button class="small" onclick="S.be.regenFor='character:new';S.key='';render(true)">✨ ให้ AI เพิ่ม</button></span>`:'');
 // lore
 h+=sec(`Lore จากของจริง (${b.lore.length}) — ทุกข้อต้องมีแหล่งที่ค้นจริง`,'',b.lore.map(x=>S.be.edit==='lore:'+x.id?`<div class="lore">${beLoreForm(b,x)}</div>`:`<div class="lore"><div class="row"><b class="grow">${esc(x.name)} <span class="chip">${esc(BE_KIND[x.kind]||x.kind)}</span> <a href="${esc(x.source_url)}" target="_blank" rel="noopener">แหล่ง</a></b>${beBtns(p,'lore:'+x.id)}</div><div><b class="muted">จริง:</b> ${esc(x.fact)}</div><div><b style="color:var(--purple)">เราแต่งต่อ:</b> ${esc(x.twist)}</div><div class="small muted">ใช้ในตอน ${(x.used_in||[]).join(', ')||'—'}</div>${beRegenBox(p,'lore:'+x.id,' lore นี้ ')}</div>`).join('')+(S.be.edit==='lore:new'?`<div class="lore">${beLoreForm(b,null)}</div>`:'')+beRegenBox(p,'lore:new','lore ใหม่ 1 ข้อ (AI ค้นเอง)'),
  `<span class="row" style="gap:4px"><button class="small" onclick="beEdit('lore:new')">+ เพิ่มเอง</button><button class="small" onclick="S.be.regenFor='lore:new';S.key='';render(true)">✨ ให้ AI ค้น+เพิ่ม</button></span>`);
 // episodes
 h+=sec(`แผนรายตอน (${b.episodes.length})`,'',b.episodes.map((e,i)=>S.be.edit==='episode:'+e.number?`<div class="ep">${beEpForm(b,e)}</div>`:`<div class="ep"><div class="row"><b class="grow">EP.${e.number} ${esc(e.title)}</b><span class="chip">${esc(HOOK_TH[e.hook_type]||e.hook_type)}</span>${beBusy(p)?'':`<button class="small" title="เลื่อนขึ้น" ${i?'':'disabled'} onclick="beMoveEp(${i},-1)">↑</button><button class="small" title="เลื่อนลง" ${i<b.episodes.length-1?'':'disabled'} onclick="beMoveEp(${i},1)">↓</button>`}${beBtns(p,'episode:'+e.number)}</div>
  <div><b class="wait">3 วิแรก:</b> ${esc(e.hook)}</div><div>${esc(e.summary)}</div><div class="muted">สถานที่: ${esc(e.location)} · ตัวละคร: ${e.characters.map(id=>esc((b.characters.find(c=>c.id===id)||{}).name||id)).join(', ')} · lore: ${e.lore.map(id=>esc((b.lore.find(x=>x.id===id)||{}).name||id)).join(', ')||'—'}</div>
  ${e.twist?`<div><b>หักมุม:</b> ${esc(e.twist)}</div>`:''}${e.cliffhanger?`<div><b class="bad">ค้างไว้:</b> ${esc(e.cliffhanger)}</div>`:''}${beRegenBox(p,'episode:'+e.number,' EP.'+e.number+' ')}</div>`).join('')+(S.be.edit==='episode:new'?`<div class="ep">${beEpForm(b,null)}</div>`:'')+beRegenBox(p,'episode:new','ตอนใหม่ต่อท้าย'),
  b.episodes.length<24?`<span class="row" style="gap:4px"><button class="small" onclick="beEdit('episode:new')">+ เพิ่มเอง</button><button class="small" onclick="S.be.regenFor='episode:new';S.key='';render(true)">✨ ให้ AI เพิ่ม</button></span>`:'');
 // rules
 h+=sec('กฎที่จะใช้ทุกตอน','rules',S.be.edit==='rules'?beRulesForm(b):`<div class="row" style="align-items:flex-start"><div class="grid g2 grow"><div><b class="small">เนื้อเรื่อง (นักเขียนบท)</b>${(b.story_rules||[]).map(x=>`<div class="small">• ${esc(x)}</div>`).join('')||'<div class="small muted">—</div>'}</div><div><b class="small">ภาพ (นักวาด)</b>${(b.visual_rules||[]).map(x=>`<div class="small">• ${esc(x)}</div>`).join('')||'<div class="small muted">—</div>'}</div></div>${beBtns(p,'rules')}</div>${beRegenBox(p,'rules','กฎ')}`);
 return h}

// ---------- forms (one item at a time)
const fIn=(id,l,v,ph)=>`<div><label>${l}</label><input type="text" id="${id}" value="${esc(v||'')}" placeholder="${esc(ph||'')}" ${typingOn}></div>`;
const fTa=(id,l,v,ph,rows)=>`<div style="margin-top:6px"><label>${l}</label><textarea id="${id}" placeholder="${esc(ph||'')}" style="min-height:${rows||60}px" ${typingOn}>${esc(v||'')}</textarea></div>`;
const formHead=t=>{const [k,key]=(S.be.edit||'').split(':');const lbl={basics:'ข้อมูลหลัก',world:'โลก + สไตล์ภาพ',rules:'กฎ',character:'ตัวละคร',episode:'ตอน',lore:'lore'}[k]||'';return `<div class="row" style="margin-bottom:8px"><b class="wait">${key==='new'?'✚ เพิ่ม'+lbl+'ใหม่':'✎ กำลังแก้ '+lbl+(t?': '+esc(t):'')}</b></div>`};
const formBtns=(del)=>`<div class="row" style="margin-top:10px"><button class="primary" onclick="beSave()">บันทึก</button><button onclick="beCancel()">ยกเลิก</button><span class="grow"></span>${del?`<button class="danger" onclick="beDelete()">ลบ</button>`:''}<span class="err small" id="beErr"></span></div>`;
function beBasicsForm(b){return `<div class="card">${formHead('')}<div class="grid g2">${fIn('fTitle','ชื่อซีรีส์',b.title)}${fIn('fGenre','แนว',b.genre)}${fIn('fTone','โทน',b.tone)}${fIn('fAud','กลุ่มผู้ชม',b.audience)}</div>${fTa('fLog','แก่นเรื่อง (logline)',b.logline)}${formBtns(false)}</div>`}
function beWorldForm(b){const w=b.world||{},v=b.visual_style||{};return `<div class="card">${formHead('')}${fTa('fSet','โลก/ฉากหลัก',w.setting,'',70)}${fIn('fEra','ยุค',w.era)}${fTa('fWR','กฎของโลก (บรรทัดละข้อ)',(w.rules||[]).join('\n'),'',90)}<div class="grid g2" style="margin-top:6px">${fIn('fPal','โทนสี',v.palette)}${fIn('fPlaces','สถานที่ประจำ (คั่นด้วย ,)',(v.recurring_places||[]).join(', '))}</div>${fTa('fVN','โน้ตภาพ',v.notes)}${formBtns(false)}</div>`}
function beCharForm(b,c){c=c||{id:'',name:'',role:'',age:'',look:'',personality:'',abilities:[],limits:[],goal:'',secret:'',arc:''};
 return `${formHead(c.name)}<div class="grid g3">${fIn('fName','ชื่อ *',c.name)}${fIn('fRole','บทบาท',c.role,'ตัวเอก / ตัวร้าย / พันธมิตร / ปริศนา')}${fIn('fAge','อายุ',c.age)}</div>
 ${fTa('fLook','หน้าตา (นักวาดใช้) *',c.look,'สีผม เสื้อผ้า ของติดตัว รูปร่าง',120)}${fTa('fPers','นิสัย',c.personality)}
 <div class="grid g2">${fTa('fAbil','ทำอะไรได้ * (บรรทัดละข้อ)',c.abilities.join('\n'),'',80)}${fTa('fLim','ทำอะไรไม่ได้/จุดอ่อน * (บรรทัดละข้อ)',c.limits.join('\n'),'',80)}</div>
 <div class="grid g3">${fTa('fGoal','เป้าหมาย',c.goal)}${fTa('fSecret','ความลับ',c.secret)}${fTa('fArc','พัฒนาการทั้งซีซัน',c.arc)}</div>${formBtns(!!c.id)}`}
function beEpForm(b,e){const n=e?e.number:b.episodes[b.episodes.length-1].number+1;e=e||{number:n,title:'',hook_type:'countdown',hook:'',summary:'',location:'',characters:[],lore:[],twist:'',cliffhanger:''};
 return `${formHead('EP.'+n+(e.title?' '+e.title:''))}<div class="grid g2" style="margin-top:6px">${fIn('fETitle','ชื่อตอน *',e.title)}<div><label>แบบเปิดเรื่อง</label><select id="fHook" style="width:100%">${Object.entries(HOOK_TH).map(([k,v])=>`<option value="${k}" ${e.hook_type===k?'selected':''}>${v}</option>`).join('')}</select></div></div>
 ${fTa('fEHook','3 วิแรก *',e.hook)}${fTa('fESum','เรื่องย่อ *',e.summary,'',110)}${fIn('fELoc','สถานที่',e.location)}
 <div class="grid g2" style="margin-top:6px"><div><label>ตัวละครในตอน</label>${b.characters.map(c=>`<label class="row small"><input type="checkbox" class="fEC" value="${esc(c.id)}" ${e.characters.includes(c.id)?'checked':''}> ${esc(c.name)}</label>`).join('')}</div>
 <div><label>lore ที่ใช้</label>${b.lore.map(x=>`<label class="row small"><input type="checkbox" class="fEL" value="${esc(x.id)}" ${e.lore.includes(x.id)?'checked':''}> ${esc(x.name)}</label>`).join('')||'<div class="small muted">—</div>'}</div></div>
 <div class="grid g2">${fTa('fETw','หักมุม',e.twist)}${fTa('fECl','ค้างไว้ไปตอนหน้า',e.cliffhanger)}</div>${formBtns(b.episodes.length>1&&S.be.edit!=='episode:new')}`}
function beLoreForm(b,x){x=x||{id:'',kind:'place',name:'',fact:'',source_url:'',twist:'',used_in:[]};const src=S.be.sources||[];const cur=S.be.pickUrl||x.source_url;
 const chosen=src.find(s=>s.url===cur);
 const L=l=>({th:'ไทย',en:'อังกฤษ'}[l]||l);
 return `${formHead(x.name)}<div class="grid g2">${fIn('fLName','ชื่อ *',x.name)}<div><label>ประเภท</label><select id="fLKind" style="width:100%">${Object.entries(BE_KIND).map(([k,v])=>`<option value="${k}" ${x.kind===k?'selected':''}>${v}</option>`).join('')}</select></div></div>
 <div class="card" style="margin-top:8px"><label>แหล่งอ้างอิง * — ต้องมาจากการค้นจริง (กันข้อมูลแต่งขึ้น)</label>
 <label style="margin-top:4px">1. ค้นแหล่งใหม่จาก Wikipedia (ไทย+อังกฤษ ฟรี) — ผลจะถูกเพิ่มในรายการข้อ 2 ด้วย</label><div class="row"><input type="text" id="fLQ" placeholder="เช่น ชื่อวัด ตำนาน บุคคล สถานที่" style="flex:1;width:auto" ${typingOn}><button onclick="beSearch()">${S.be.searching?'<span class="spin"></span>กำลังค้น':'ค้น'}</button></div>
 ${(S.be.hits||[]).length?`<div class="small" style="margin-top:6px">${S.be.hits.map(h=>`<div class="row" style="margin-top:4px"><span class="grow"><b>${esc(h.title)}</b> <span class="muted">(${L(h.lang)})</span> <a href="${esc(h.url)}" target="_blank" rel="noopener">เปิดดู ↗</a></span><button class="small ${h.url===cur?'good':''}" onclick="S.be.pickUrl='${esc(h.url)}';S.key='';render(true)">${h.url===cur?'✓ เลือกแล้ว':'ใช้อันนี้'}</button></div><div class="muted">${esc(h.extract.slice(0,160))}…</div>`).join('')}</div>`:S.be.hits?'<div class="small muted" style="margin-top:4px">ไม่พบ — ลองคำอื่นหรือภาษาอังกฤษ</div>':''}
 <label style="margin-top:8px">2. หรือเลือกจากแหล่งที่ค้นไว้แล้ว (${src.length})</label><select id="fLSrc" style="width:100%" onchange="S.be.pickUrl=this.value;S.key='';render(true)"><option value="">— เลือกแหล่ง —</option>${src.map(s=>`<option value="${esc(s.url)}" ${s.url===cur?'selected':''}>${esc(s.title)} (${L(s.lang)})</option>`).join('')}</select>
 ${chosen?`<div class="small" style="margin-top:6px;color:#c3cddd">แหล่งนี้บอกว่า: ${esc(chosen.extract)} <a href="${esc(chosen.url)}" target="_blank" rel="noopener">เปิดดู ↗</a></div>`:''}</div>
 ${fTa('fLFact','จริง: ข้อเท็จจริงตามแหล่ง *',x.fact,'สรุปสั้น ๆ ให้ตรงกับแหล่งด้านบน')}${fTa('fLTw','เราแต่งต่อ: เอามาใช้ในเรื่องอย่างไร',x.twist)}
 ${fIn('fLUsed','ใช้ในตอน (คั่นด้วย ,)',(x.used_in||[]).join(', '))}${formBtns(!!x.id)}`}
function beRulesForm(b){return `<div class="card">${formHead('')}<div class="grid g2">${fTa('fRS','กฎเนื้อเรื่อง (บรรทัดละข้อ)',(b.story_rules||[]).join('\n'),'',160)}${fTa('fRV','กฎภาพ (บรรทัดละข้อ)',(b.visual_rules||[]).join('\n'),'',160)}</div>${formBtns(false)}</div>`}

// ---------- actions
async function beEdit(target){S.be.edit=target;S.be.regenFor=null;S.be.hits=null;S.be.pickUrl=null;S.be.base=S.be.plan.updated_at;
 if(target.startsWith('lore')&&!S.be.sources){try{S.be.sources=await api(`/api/plan/${S.be.plan.id}/sources`)}catch(e){S.be.sources=[]}}
 S.typing=true;S.key='';render(true)}
function beCancel(){S.be.edit=null;S.be.hits=null;S.be.pickUrl=null;S.typing=false;S.key='';render(true)}
function beCollect(b){const t=S.be.edit,v=id=>($(id)||{}).value||'',[kind,key]=t.split(':');const nb=JSON.parse(JSON.stringify(b));
 if(t==='basics')Object.assign(nb,{title:v('fTitle'),genre:v('fGenre'),tone:v('fTone'),audience:v('fAud'),logline:v('fLog')});
 else if(t==='world'){nb.world={setting:v('fSet'),era:v('fEra'),rules:lines(v('fWR'))};nb.visual_style={palette:v('fPal'),recurring_places:v('fPlaces').split(',').map(x=>x.trim()).filter(Boolean),notes:v('fVN')}}
 else if(t==='rules'){nb.story_rules=lines(v('fRS'));nb.visual_rules=lines(v('fRV'))}
 else if(kind==='character'){const id=key==='new'?beNewId(nb.characters,'ch'):key;const c={id,name:v('fName'),role:v('fRole'),age:v('fAge'),look:v('fLook'),personality:v('fPers'),abilities:lines(v('fAbil')),limits:lines(v('fLim')),goal:v('fGoal'),secret:v('fSecret'),arc:v('fArc')};
  if(!c.name||!c.look||!c.abilities.length||!c.limits.length)throw Error('ต้องมีชื่อ หน้าตา สิ่งที่ทำได้ และสิ่งที่ทำไม่ได้');
  const old=nb.characters.find(x=>x.id===id);if(old)c.first_episode=old.first_episode;
  nb.characters=key==='new'?[...nb.characters,c]:nb.characters.map(x=>x.id===id?c:x)}
 else if(kind==='episode'){const n=key==='new'?nb.episodes[nb.episodes.length-1].number+1:+key;const e={number:n,title:v('fETitle'),hook_type:v('fHook'),hook:v('fEHook'),summary:v('fESum'),location:v('fELoc'),
  characters:[...document.querySelectorAll('.fEC:checked')].map(x=>x.value),lore:[...document.querySelectorAll('.fEL:checked')].map(x=>x.value),twist:v('fETw'),cliffhanger:v('fECl')};
  if(!e.title||!e.hook||!e.summary)throw Error('ต้องมีชื่อตอน 3 วิแรก และเรื่องย่อ');nb.episodes=key==='new'?[...nb.episodes,e]:nb.episodes.map(x=>x.number===n?e:x)}
 else if(kind==='lore'){const id=key==='new'?beNewId(nb.lore,'lr'):key;const url=v('fLSrc');if(!url)throw Error('เลือกแหล่งอ้างอิงจากผลค้นก่อน');
  const x={id,kind:v('fLKind'),name:v('fLName'),fact:v('fLFact'),source_url:url,twist:v('fLTw'),used_in:v('fLUsed').split(',').map(s=>parseInt(s)).filter(n=>n>0)};
  if(!x.name||!x.fact)throw Error('ต้องมีชื่อและข้อเท็จจริง');nb.lore=key==='new'?[...nb.lore,x]:nb.lore.map(y=>y.id===id?x:y)}
 return nb}
function beNewId(list,prefix){let i=list.length+1;while(list.some(x=>x.id===prefix+i))i++;return prefix+i}
async function beSave(nb){const p=S.be.plan;try{nb=nb||beCollect(p.bible)}catch(e){$('beErr').textContent=e.message;return}
 try{await post(`/api/plan/${p.id}/save-bible`,{bible:nb,expect_updated:S.be.base||p.updated_at});toast('บันทึกแล้ว'+(p.status==='approved'?' — อัปเดตซีรีส์แล้ว':''));S.be.edit=null;S.be.hits=null;S.be.pickUrl=null;S.typing=false;S.key='';await loadCommon();render(true)}
 catch(e){if($('beErr'))$('beErr').textContent=e.message;else toast('ผิดพลาด: '+e.message,9000)}}
async function beDelete(){const p=S.be.plan,b=p.bible,[kind,key]=S.be.edit.split(':');const nb=JSON.parse(JSON.stringify(b));
 if(kind==='character'){if(nb.characters.length<2)return toast('ต้องเหลืออย่างน้อย 1 ตัวละคร');const used=nb.episodes.filter(e=>e.characters.includes(key)).map(e=>e.number);
  if(!confirm(`ลบตัวละครนี้?${used.length?` (ถูกใช้ใน EP.${used.join(', ')} — จะเอาออกจากตอนเหล่านั้น)`:''}`))return;nb.characters=nb.characters.filter(c=>c.id!==key);nb.episodes.forEach(e=>e.characters=e.characters.filter(x=>x!==key))}
 else if(kind==='lore'){if(!confirm('ลบ lore นี้? (จะเอาออกจากทุกตอนที่ใช้)'))return;nb.lore=nb.lore.filter(x=>x.id!==key);nb.episodes.forEach(e=>e.lore=e.lore.filter(x=>x!==key))}
 else if(kind==='episode'){if(!confirm(`ลบ EP.${key}? ตอนหลังจากนี้จะเลื่อนเลขขึ้น`))return;nb.episodes=nb.episodes.filter(e=>String(e.number)!==key)}
 S.be.base=p.updated_at;await beSave(nb)}
async function beMoveEp(i,dir){const p=S.be.plan,nb=JSON.parse(JSON.stringify(p.bible));const j=i+dir;[nb.episodes[i],nb.episodes[j]]=[nb.episodes[j],nb.episodes[i]];
S.be.base=p.updated_at;await beSave(nb)}
async function beSearch(){const q=($('fLQ')||{}).value||'';if(!q.trim())return toast('ใส่คำค้น');S.be.searching=true;S.key='';render(true);
 try{S.be.hits=await post(`/api/plan/${S.be.plan.id}/lore-search`,{query:q});S.be.sources=await api(`/api/plan/${S.be.plan.id}/sources`)}catch(e){toast('ค้นไม่ได้: '+e.message,8000);S.be.hits=[]}
 S.be.searching=false;S.key='';render(true)}
async function beRegen(id,target){const instruction=($('beInstr')||{}).value||'';
 try{await post(`/api/plan/${id}/regen`,{target,instruction});toast('AI เริ่มเขียนใหม่แล้ว — ใช้เวลา 1–4 นาที');S.be.regenFor=null;S.typing=false;S.key='';for(const t of [800,2000,4000])setTimeout(()=>refresh(true),t)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}

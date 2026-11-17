// Publish: post screen inside the "ปล่อย" stage + accounts tab. Loaded after app.js (uses $, esc, api, post, toast, S).
const PF={youtube:{name:'YouTube Shorts',icon:'▶'},tiktok:{name:'TikTok',icon:'♪'},instagram:{name:'Instagram Reels',icon:'◎'}};
const PSTAT={queued:'เข้าคิว',uploading:'กำลังอัปโหลด',processing:'แพลตฟอร์มกำลังประมวลผล',inbox:'ส่งเข้า Inbox แล้ว — เปิดแอป TikTok กดแจ้งเตือนเพื่อโพสต์',published:'โพสต์แล้ว',published_private:'อัปโหลดแล้ว (ส่วนตัว)',failed:'ล้ม'};
const TT_PRIV={PUBLIC_TO_EVERYONE:'ทุกคน',MUTUAL_FOLLOW_FRIENDS:'เพื่อน (ติดตามกันและกัน)',FOLLOWER_OF_CREATOR:'ผู้ติดตาม',SELF_ONLY:'เฉพาะฉัน'};
S.pub={tab:'youtube',accounts:null,creator:null,creatorErr:'',data:null};

async function pubLoad(eid){const [acc,d]=await Promise.all([api('/api/publish/accounts').catch(e=>({error:e.message})),api(`/api/episode/${eid}/posts`)]);S.pub.accounts=acc;S.pub.data=d;return d}
function pubKey(){const d=S.pub.data||{};return JSON.stringify([S.pub.tab,(d.posts||[]).map(p=>p.id+p.status+(p.error||'')),S.pub.creator&&1,S.pub.creatorErr,Object.values(S.pub.accounts||{}).map(a=>a.connected)])}

// called from stageView('publish') in app.js
function publishStage(eid,j,f){
 if(!(j.status==='done'||!j.id))return '<span class="muted">ต้องอนุมัติคลิปสุดท้ายก่อน แล้วจึงโพสต์ได้</span>';
 if(!S.pub.data||S.pub.eid!==eid){S.pub.eid=eid;pubLoad(eid).then(()=>{S.key='';render(true)});return '<span class="spin"></span>กำลังโหลด…'}
 const d=S.pub.data,acc=S.pub.accounts||{},df=d.defaults||{},posts=d.posts||[];
 const tabs=Object.entries(PF).map(([k,v])=>{const last=posts.find(p=>p.platform===k);return `<button class="tab ${S.pub.tab===k?'on':''}" onclick="S.pub.tab='${k}';S.pub.creator=null;S.key='';render(true)">${v.icon} ${v.name}${last?` <span class="n ${last.status==='failed'?'warn':''}">${esc(PSTAT[last.status]||last.status).split(' ')[0]}</span>`:''}</button>`}).join('');
 const p=S.pub.tab,a=acc[p]||{};
 let body='';
 if(!a.connected)body=`<div class="gatebox">ยังไม่ได้เชื่อมบัญชี ${PF[p].name} <button onclick="go('accounts')">ไปเชื่อมบัญชี</button></div>`;
 else body=pubForm(eid,p,a,df,posts.some(x=>x.platform===p&&x.status!=='failed'));
 const hist=posts.length?`<h3>ประวัติการโพสต์</h3>${posts.map(x=>`<div class="row small" style="margin:4px 0"><span>#${x.id} ${PF[x.platform].icon} ${PF[x.platform].name}</span><span class="status ${x.status==='failed'?'bad':x.status.startsWith('published')||x.status==='inbox'?'ok':'run'}">${x.alive?'<span class="spin"></span>':''}${esc(PSTAT[x.status]||x.status)}</span>${x.url?`<a href="${esc(x.url)}" target="_blank" rel="noopener">เปิดดู</a>`:''}<span class="muted">${esc(localTime(x.posted_at||x.created_at))}</span>${x.status==='failed'?`<button onclick="pubAgain(${x.id},'retry')">ลองใหม่</button>`:''}${['processing','inbox','published_private'].includes(x.status)&&!x.alive?`<button onclick="pubAgain(${x.id},'refresh')">เช็กสถานะ</button>`:''}</div>${x.error?`<div class="err small">${esc(x.error.slice(-300))}</div>`:''}`).join('')}`:'';
 return `<div class="row" style="gap:12px;align-items:flex-start"><div>${f.web?`<video controls playsinline preload="metadata" src="${media(f.web)}" style="max-width:220px"></video>`:''}<div class="small muted">ตัวอย่างไฟล์ที่จะส่ง (final.mp4 1080×1920)</div></div><div class="grow" style="min-width:280px"><div class="row" style="gap:4px;flex-wrap:wrap">${tabs}</div><div style="margin-top:10px">${body}</div>${hist}</div></div>`}

function pubForm(eid,p,a,df,already){
 const who=`<div class="small">บัญชี: <b>${esc((a.account||{}).name||'?')}</b></div>`;
 const again=already?`<label class="row small" style="color:var(--yellow)"><input type="checkbox" id="pbAgain"> ตอนนี้โพสต์ไป ${PF[p].name} แล้ว — ติ๊กถ้าต้องการโพสต์ซ้ำ</label>`:'';
 if(p==='youtube')return `${who}<div style="margin-top:8px"><label>ชื่อคลิป (≤100 ตัว)</label><input type="text" id="pbTitle" maxlength="100" value="${esc(df.title||'')}" ${typingOn}></div>
  <div style="margin-top:8px"><label>คำอธิบาย + แฮชแท็ก</label><textarea id="pbCap" style="min-height:110px" ${typingOn}>${esc(df.youtube_description||df.caption||'')}</textarea></div>
  <div style="margin-top:8px"><label>แท็ก (คั่นด้วย ,)</label><input type="text" id="pbTags" value="${esc((df.youtube_tags||[]).join(', '))}" ${typingOn}></div>
  <div class="grid g2" style="margin-top:8px"><div><label>การมองเห็น</label><select id="pbPriv" style="width:100%"><option value="private" selected>ส่วนตัว (แนะนำ — ตรวจแล้วค่อยเปิด)</option><option value="unlisted">ไม่แสดงในรายการ</option><option value="public">สาธารณะ</option></select></div>
  <div><label>ผู้ชม</label><select id="pbKids" style="width:100%"><option value="0" selected>ไม่ได้สร้างมาเพื่อเด็ก</option><option value="1">สร้างมาเพื่อเด็ก</option></select></div></div>
  <label class="row small" style="margin-top:8px"><input type="checkbox" id="pbSynth" checked> ติดป้าย "เนื้อหาที่สร้าง/ดัดแปลงด้วย AI" (YouTube ขอให้เปิดเผย)</label>${again}
  ${pubButton(eid,p)}`;
 if(p==='instagram')return `${who}<div style="margin-top:8px"><label>แคปชั่น + แฮชแท็ก (≤2200 ตัว, ≤30 แท็ก)</label><textarea id="pbCap" style="min-height:120px" ${typingOn}>${esc(df.caption||'')}</textarea></div>
  <label class="row small" style="margin-top:8px"><input type="checkbox" id="pbFeed" checked> แสดงในฟีดด้วย (ไม่ใช่แค่แท็บ Reels)</label>
  <div class="help small" style="margin-top:8px">Instagram จะดึงไฟล์จากลิงก์ลับชั่วคราว (หมดอายุใน 2 ชม.) แล้วโพสต์ทันทีเป็นสาธารณะตามบัญชี</div>${again}${pubButton(eid,p)}`;
 // TikTok
 const mode=S.pub.ttMode||'inbox';
 let h=`${who}<div class="row" style="margin-top:8px"><label class="row"><input type="radio" name="ttm" ${mode==='inbox'?'checked':''} onchange="S.pub.ttMode='inbox';S.key='';render(true)"> ส่งเข้า Inbox (แนะนำ) — แก้/ใส่เพลง/กดโพสต์เองในแอป</label>
  <label class="row"><input type="radio" name="ttm" ${mode==='direct'?'checked':''} onchange="S.pub.ttMode='direct';S.key='';render(true)"> โพสต์ตรง (Direct Post)</label></div>
  <div style="margin-top:8px"><label>แคปชั่น + แฮชแท็ก (≤2200)</label><textarea id="pbCap" style="min-height:110px" ${typingOn}>${esc(df.caption||'')}</textarea></div>`;
 if(mode==='inbox')return h+`<div class="help small" style="margin-top:8px">ระบบอัปโหลดคลิปเป็นดราฟต์ แล้ว TikTok จะส่งแจ้งเตือนไปที่แอป — กดแจ้งเตือนเพื่อแก้และโพสต์ (ค้างได้ไม่เกิน 5 ดราฟต์ใน 24 ชม.) แคปชั่นนี้ใช้คัดลอกไปวางในแอป</div>${again}${pubButton(eid,p)}`;
 if(!S.pub.creator&&!S.pub.creatorErr){api('/api/publish/creator').then(c=>{S.pub.creator=c;S.key='';render(true)}).catch(e=>{S.pub.creatorErr=e.message;S.key='';render(true)});return h+'<div><span class="spin"></span>กำลังถามข้อมูลบัญชีจาก TikTok…</div>'}
 if(S.pub.creatorErr)return h+`<div class="err">${esc(S.pub.creatorErr)}</div><button onclick="S.pub.creatorErr='';S.key='';render(true)">ลองใหม่</button>`;
 const c=S.pub.creator;
 h+=`<div class="small" style="margin-top:8px">โพสต์ไปที่: <b>${esc(c.creator_nickname)}</b> (@${esc(c.creator_username)})</div>
  <div style="margin-top:8px"><label>ใครดูได้ (ต้องเลือก)</label><select id="pbPriv" style="width:100%"><option value="" selected disabled>— เลือก —</option>${(c.privacy_level_options||[]).map(o=>`<option value="${o}">${TT_PRIV[o]||o}</option>`).join('')}</select></div>
  <div class="row small" style="margin-top:8px;gap:16px"><label class="row"><input type="checkbox" id="pbCom" ${c.comment_disabled?'disabled':''}> อนุญาตคอมเมนต์</label><label class="row"><input type="checkbox" id="pbDuet" ${c.duet_disabled?'disabled':''}> Duet</label><label class="row"><input type="checkbox" id="pbStitch" ${c.stitch_disabled?'disabled':''}> Stitch</label></div>
  <details style="margin-top:8px"><summary class="small">เนื้อหาเชิงพาณิชย์ (ปิดไว้)</summary><label class="row small"><input type="checkbox" id="pbOwn"> โปรโมตธุรกิจของตัวเอง — จะติดป้าย "Promotional content"</label><label class="row small"><input type="checkbox" id="pbBrand"> Branded content (ได้รับค่าจ้าง) — จะติดป้าย "Paid partnership" ตั้งเป็นเฉพาะฉันไม่ได้</label></details>
  <label class="row small" style="margin-top:8px"><input type="checkbox" id="pbAigc" checked> ติดป้าย AI-generated</label>
  <label class="row small" style="margin-top:8px"><input type="checkbox" id="pbMusic"> โดยการโพสต์ ฉันยอมรับ <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener">Music Usage Confirmation</a> ของ TikTok</label>
  <div class="help small" style="margin-top:8px">แอป TikTok ที่ยังไม่ผ่าน audit: บัญชีต้องตั้งเป็นส่วนตัวและเลือกได้แค่ "เฉพาะฉัน" · หลังโพสต์ TikTok อาจใช้เวลาประมวลผลสักครู่ก่อนขึ้นโปรไฟล์</div>${again}`;
 return h+pubButton(eid,p)}
function pubButton(eid,p){return `<div class="row" style="margin-top:12px"><button class="primary" onclick="pubSend(${eid},'${p}')">${p==='tiktok'&&(S.pub.ttMode||'inbox')==='inbox'?'ส่งเข้า Inbox TikTok':'โพสต์ไป '+PF[p].name}</button></div>`}
function pubOptions(p){const v=id=>($(id)||{}).value,c=id=>!!($(id)||{}).checked;
 if(p==='youtube')return {title:v('pbTitle'),caption:v('pbCap'),tags:(v('pbTags')||'').split(',').map(x=>x.trim()).filter(Boolean),privacy:v('pbPriv'),made_for_kids:v('pbKids')==='1',synthetic:c('pbSynth')};
 if(p==='instagram')return {caption:v('pbCap'),share_to_feed:c('pbFeed')};
 const mode=S.pub.ttMode||'inbox';if(mode==='inbox')return {mode,caption:v('pbCap')};
 return {mode,caption:v('pbCap'),privacy:v('pbPriv'),allow_comment:c('pbCom'),allow_duet:c('pbDuet'),allow_stitch:c('pbStitch'),brand_organic:c('pbOwn'),brand_content:c('pbBrand'),is_aigc:c('pbAigc'),music_ack:c('pbMusic')}}
async function pubSend(eid,p){const o=pubOptions(p);
 if(p==='tiktok'&&o.mode==='direct'){if(!o.privacy)return toast('เลือกว่าใครดูได้ก่อน');if(!o.music_ack)return toast('ต้องติ๊กยอมรับ Music Usage Confirmation');if(o.brand_content&&o.privacy==='SELF_ONLY')return toast('Branded content ตั้งเป็นเฉพาะฉันไม่ได้')}
 const what=p==='youtube'?`อัปโหลด "${o.title}" ไป YouTube (${o.privacy})`:p==='instagram'?'โพสต์ Reels ไป Instagram ทันที (สาธารณะ)':o.mode==='inbox'?'ส่งคลิปเข้า Inbox TikTok':`โพสต์ตรงไป TikTok (${TT_PRIV[o.privacy]})`;
 if(!confirm(what+' ?'))return;
 try{await post(`/api/episode/${eid}/post`,{platform:p,options:o,confirm:true,again:!!($('pbAgain')||{}).checked});toast('เริ่มส่งแล้ว — สถานะอัปเดตด้านล่าง');S.typing=false;S.pub.data=null;S.key='';setTimeout(()=>render(true),1500)}catch(e){toast('ผิดพลาด: '+e.message,9000)}}
async function pubAgain(id,action){try{await post(`/api/post/${id}/${action}`,{});toast(action==='retry'?'ลองส่งใหม่แล้ว':'อัปเดตสถานะแล้ว');S.pub.data=null;S.key='';setTimeout(()=>render(true),1200)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
setInterval(()=>{if(S.tab==='produce'&&S.stage==='publish'&&S.pub.data&&(S.pub.data.posts||[]).some(p=>['queued','uploading','processing'].includes(p.status))){pubLoad(S.pub.eid).then(()=>{if(pubKey()!==S.pub.lastKey){S.pub.lastKey=pubKey();S.key='';render(false)}})}},5000);

// ================= Accounts tab =================
const YT_OWN=`<ol class="small"><li>ล็อกอิน <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Google Cloud</a> ด้วย<b>บัญชีเดียวกับช่อง YouTube</b> → สร้างโปรเจกต์ใหม่ (ฟรี ไม่ต้องใส่บัตร)</li>
<li>APIs &amp; Services → Library → <b>YouTube Data API v3</b> → Enable</li>
<li>Google Auth Platform → Get started: ตั้งชื่อแอป, User type = <b>External</b> → Audience → Test users → เพิ่มอีเมลช่องของคุณ</li>
<li>Clients → Create client → Application type = <b>Desktop app</b> → คัดลอก Client ID และ Client secret มาใส่ด้านล่าง</li>
<li>บันทึก แล้วกด <b>เชื่อมบัญชี</b></li></ol>`;
const SETUP={
 youtube:`<ol class="small"><li>เปิด <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=mail-assistant-512102" target="_blank" rel="noopener">YouTube Data API v3</a> ในโปรเจกต์ Google Cloud <code>mail-assistant-512102</code> (โปรเจกต์ของ gmail-assistant) แล้วกด <b>Enable</b></li><li><b>สำคัญ:</b> แอปอยู่โหมด Testing → ต้องเพิ่ม<b>อีเมลของบัญชีที่จะล็อกอิน</b>ใน <a href="https://console.cloud.google.com/auth/audience?project=mail-assistant-512102" target="_blank" rel="noopener">Audience → Test users</a> ก่อน ไม่งั้นจะขึ้น "Access blocked … Error 403: access_denied" (และโหมดนี้ต้องเชื่อมใหม่ทุก 7 วัน)</li><li>กด "เชื่อมบัญชี" ด้านล่าง → เลือกบัญชีที่มีช่อง → อนุญาต</li><li>หลังกดเชื่อมบัญชีจะมีช่องให้วางลิงก์ — หน้าสุดท้ายของ Google จะเปิดไม่ได้ (localhost) ให้<b>คัดลอกลิงก์ทั้งบรรทัดจากแถบที่อยู่</b> มาวางในช่องนั้น</li></ol>`,
 tiktok:`<ol class="small"><li>สมัคร <a href="https://developers.tiktok.com/" target="_blank" rel="noopener">TikTok for Developers</a> → Manage apps → Connect an app</li><li>Add products: <b>Login Kit</b> และ <b>Content Posting API</b> (เปิด Direct Post ด้วยถ้าต้องการ)</li><li>Scopes: user.info.basic, video.upload, video.publish</li><li>Login Kit → Redirect URI: {redirect}</li><li>ใส่ Client key / Client secret ด้านล่าง → <b>บันทึกแอป</b> → จะมีปุ่ม <b>เชื่อมบัญชี</b> ขึ้นมา (ช่วง sandbox ต้องเพิ่มบัญชีตัวเองเป็น target user)</li><li>ก่อนผ่าน audit: Direct Post ได้แค่ "เฉพาะฉัน" และบัญชีต้องตั้งเป็นส่วนตัว — โหมด Inbox ใช้ได้ปกติ</li></ol>`,
 instagram:`<ol class="small"><li>บัญชี IG ต้องเป็น <b>Professional</b> (Business หรือ Creator) — ตั้งในแอป IG: Settings → Account type</li><li>สร้างแอปที่ <a href="https://developers.facebook.com/apps/" target="_blank" rel="noopener">Meta for Developers</a> → use case "Manage messaging &amp; content on Instagram"</li><li>Instagram → API setup with Instagram login → เพิ่มบัญชี IG ของคุณเป็น Instagram tester แล้วกดยอมรับในแอป IG</li><li>Business login settings → OAuth redirect URI: {redirect}</li><li>คัดลอก Instagram App ID / App Secret มาใส่ด้านล่าง → <b>บันทึกแอป</b> → จะมีปุ่ม <b>เชื่อมบัญชี</b> ขึ้นมา</li></ol>`};
async function accountsView(){const acc=await api('/api/publish/accounts').catch(e=>({error:e.message}));const key=JSON.stringify(['accounts',acc,location.hash]);if(key===S.key)return null;S.key=key;S.pub.accounts=acc;
 const msg=new URLSearchParams((location.hash.split('?')[1]||''));
 const banner=msg.get('ok')?`<div class="help">✓ เชื่อม ${esc(msg.get('ok'))} แล้ว</div>`:msg.get('err')?`<div class="gatebox" style="border-color:var(--red)"><b class="bad">เชื่อมไม่สำเร็จ</b> ${esc(msg.get('err'))}</div>`:'';
 if(acc.error)return `<div class="panel"><div class="err">${esc(acc.error)}</div></div>`;
 return `<div class="panel"><h2>บัญชีโซเชียลสำหรับโพสต์</h2>${banner}<div class="help">key และ token เก็บในเครื่อง pinto เท่านั้น (secrets/publish, สิทธิ์ 600) หน้าเว็บนี้ไม่แสดงค่าลับกลับมา · โพสต์ได้ที่ ③ ผลิตตอน → ขั้น "5. โพสต์" ของตอนที่อนุมัติคลิปสุดท้ายแล้ว</div>
 <div class="grid g3">${Object.entries(PF).map(([k,v])=>accCard(k,v,acc[k]||{})).join('')}</div></div>`}
function accCard(k,v,a){const st=a.connected?`<span class="status ok">เชื่อมแล้ว</span>`:a.app_configured?`<span class="status wait">ใส่แอปแล้ว รอเชื่อมบัญชี</span>`:`<span class="status">ยังไม่ตั้งค่า</span>`;
 const app=k==='youtube'?`<div class="small muted">ตอนนี้ใช้ Google client ของ gmail-assistant</div><details style="margin-top:6px"><summary class="small">ใช้ Google Cloud project ของบัญชีช่องเอง (แนะนำถ้าเข้า project เดิมไม่ได้)</summary>${YT_OWN}<label style="margin-top:6px">Client ID</label><input type="text" id="ap_yt_k" autocomplete="off" placeholder="….apps.googleusercontent.com" ${typingOn}><label style="margin-top:6px">Client secret</label><input type="password" id="ap_yt_s" autocomplete="new-password" ${typingOn}><button style="margin-top:6px" onclick="accApp('youtube')">บันทึก client นี้</button></details>`:
  k==='tiktok'?`<label>Client key</label><input type="text" id="ap_tt_k" autocomplete="off" ${typingOn}><label style="margin-top:6px">Client secret</label><input type="password" id="ap_tt_s" autocomplete="new-password" ${typingOn}><button style="margin-top:6px" onclick="accApp('tiktok')">บันทึกแอป</button>`:
  `<label>Instagram App ID</label><input type="text" id="ap_ig_k" autocomplete="off" ${typingOn}><label style="margin-top:6px">Instagram App Secret</label><input type="password" id="ap_ig_s" autocomplete="new-password" ${typingOn}><button style="margin-top:6px" onclick="accApp('instagram')">บันทึกแอป</button>`;
 const paste=k==='youtube'&&S.pub.ytWaiting?`<div class="gatebox"><div class="small"><b>1.</b> <a id="ap_yt_go" href="${esc(S.pub.ytWaiting)}" target="_blank" rel="noopener">เปิดหน้าอนุญาตของ Google ↗</a> แล้วกดอนุญาต</div><div class="small" style="margin:6px 0"><b>2.</b> หน้าสุดท้ายจะเปิดไม่ได้ (localhost) — คัดลอกลิงก์ทั้งบรรทัดจากแถบที่อยู่มาวางที่นี่</div><label>ลิงก์ (ขึ้นต้น http://localhost:1/?state=…)</label><input type="text" id="ap_yt_url" ${typingOn}><button class="good" style="margin-top:6px" onclick="accFinishYT()">ยืนยัน</button></div>`:'';
 return `<div class="card"><div class="row"><b class="grow">${v.icon} ${v.name}</b>${st}</div>
  ${a.connected?`<div class="small" style="margin-top:6px">บัญชี: <b>${esc((a.account||{}).name||'')}</b>${a.refresh_expires_at?` · ต้องเชื่อมใหม่ก่อน ${new Date(a.refresh_expires_at*1000).toLocaleDateString('th-TH')}`:''}${k==='instagram'&&a.expires_at?` · token ถึง ${new Date(a.expires_at*1000).toLocaleDateString('th-TH')} (ต่ออายุเองเมื่อใช้)`:''}</div>`:''}
  <details style="margin-top:8px" ${a.connected||(k==='youtube'&&a.app_configured)?'':'open'}><summary class="small">วิธีตั้งค่า</summary>${SETUP[k].replace('{redirect}',`<span class="row" style="gap:4px;display:inline-flex"><code style="overflow-wrap:anywhere">${esc(a.redirect_uri||'')}</code><button class="small" onclick="navigator.clipboard.writeText('${esc(a.redirect_uri||'')}').then(()=>toast('คัดลอกแล้ว'))">คัดลอก</button></span>`)}</details>
  <div style="margin-top:8px">${a.connected?'':app}</div>
  <div class="row" style="margin-top:10px">${a.app_configured?`<button class="primary" onclick="accConnect('${k}')">${a.connected?'เชื่อมใหม่':'เชื่อมบัญชี'}</button>`:''}${a.connected?`<button class="danger" onclick="accDisconnect('${k}')">ยกเลิกการเชื่อม</button>`:''}</div>${paste}</div>`}
async function accApp(k){const ids={youtube:['ap_yt_k','ap_yt_s','client_id','client_secret'],tiktok:['ap_tt_k','ap_tt_s','client_key','client_secret'],instagram:['ap_ig_k','ap_ig_s','app_id','app_secret']}[k];const b={};b[ids[2]]=$(ids[0]).value.trim();b[ids[3]]=$(ids[1]).value.trim();
 try{await post(`/api/publish/${k}/app`,b);toast('บันทึกแล้ว');S.typing=false;S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function accConnect(k){try{const d=await post(`/api/publish/${k}/connect`,{});if(k==='youtube'){S.pub.ytWaiting=d.auth_url;S.key='';render(true)}else location.href=d.auth_url}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
async function accFinishYT(){try{await post('/api/publish/youtube/finish',{pasted_url:$('ap_yt_url').value});S.pub.ytWaiting=false;toast('เชื่อม YouTube แล้ว');S.typing=false;S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,9000)}}
async function accDisconnect(k){if(!confirm('ยกเลิกการเชื่อม '+PF[k].name+'?'))return;try{await post(`/api/publish/${k}/disconnect`,{});S.key='';render(true)}catch(e){toast('ผิดพลาด: '+e.message,8000)}}
if(location.hash.startsWith('#accounts'))setTimeout(()=>go('accounts'),50);

// a pick in the post form must survive the 5 s refresh: treat it like typing until the user sends or leaves
document.addEventListener('change',e=>{if((e.target.id||'').startsWith('pb')||e.target.name==='ttm')S.typing=true});
function localTime(t){if(!t)return '';const d=new Date(t.replace(' ','T')+'Z');return isNaN(d)?t:d.toLocaleString('th-TH',{dateStyle:'short',timeStyle:'short'})}

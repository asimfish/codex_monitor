
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const I18N = {en: __EN__, zh: __ZH__};
const URL_LANG = new URLSearchParams(location.search).get('lang');
let LANG = URL_LANG || localStorage.getItem('cm_lang') || ((navigator.language||'').toLowerCase().startsWith('zh') ? 'zh' : 'en');
function wl(label){ if (label === 'quota') return t('quota'); if (LANG !== 'zh') return label; if (label === 'weekly') return '每周'; if (label === 'daily') return '每天'; if (label === '5h') return '5 小时'; let m = /^(\d+)d$/.exec(label); if (m) return m[1] + ' 天'; m = /^(\d+)h$/.exec(label); if (m) return m[1] + ' 小时'; m = /^(\d+)m$/.exec(label); if (m) return m[1] + ' 分'; return label; }
const t = k => { const v = I18N[LANG] && I18N[LANG][k]; if (v !== undefined) return v; const e = I18N.en[k]; return e !== undefined ? e : k; };
let expandedAll = localStorage.getItem('cm_expand_all') !== '0';
const expanded = new Set();
try { const saved=JSON.parse(localStorage.getItem('cm_expanded')||'[]');if(Array.isArray(saved))saved.filter(n=>typeof n==='string').forEach(n=>expanded.add(n)); } catch {}
const saveExpansion = () => { localStorage.setItem('cm_expand_all',expandedAll?'1':'0');localStorage.setItem('cm_expanded',JSON.stringify([...expanded])); };
if (!['en','zh'].includes(LANG)) LANG='en';
let STATE = null, loginTimer = null;

async function api(path, method='GET', body=null) {
  const r = await fetch(path, {method, headers: {'X-Token': TOKEN, 'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : null});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const fmtTime = iso => iso ? new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '-';
const fmtDT = iso => iso ? new Date(iso).toLocaleString([], {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '—';
const fmtDate = iso => iso ? new Date(iso).toLocaleDateString([], {month:'2-digit', day:'2-digit'}) : '—';
const pct = v => Number.isInteger(v) ? v + '%' : v.toFixed(1) + '%';
const color = r => r > 50 ? 'var(--green)' : r > 20 ? 'var(--orange)' : 'var(--red)';
function daysLeft(iso){ if(!iso) return ''; const s=(new Date(iso)-Date.now())/1000; if(s<=0) return t('expired'); const d=Math.floor(s/86400); return d>=1 ? t('daysLeft').replace('{n}', d) : t('hoursLeft').replace('{n}', Math.max(1, Math.floor(s/3600))); }
function ago(iso){ if(!iso) return ''; const s=Math.floor((Date.now()-new Date(iso))/1000); if(s<60) return t('justNow'); if(s<3600) return t('minAgo').replace('{n}', Math.floor(s/60)); return fmtTime(iso); }
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }


const statusKeys = {ready:'statusReady',checking:'statusChecking',limited:'statusLimited',expired:'statusExpired',error:'statusError',signed_out:'statusSignedOut',manual:'statusManual'};
let currentFilter = 'all';
const statusText = a => t(statusKeys[a.availability]);
const attention = a => ['expired','error','signed_out','manual'].includes(a.availability);
const fmtFull = iso => iso ? new Date(iso).toLocaleString(LANG==='zh'?'zh-CN':'en-US',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const countdown = iso => {
  if (!iso) return '—';
  const minutes = Math.max(0,Math.ceil((new Date(iso)-Date.now())/60000));
  if (!minutes) return t('resetNow');
  const days = Math.floor(minutes/1440), hours = Math.floor(minutes%1440/60), mins = minutes%60;
  return LANG==='zh' ? (days ? `${days}天 ${hours}小时` : hours ? `${hours}小时 ${mins}分` : `${mins}分钟`) : (days ? `${days}d ${hours}h` : hours ? `${hours}h ${mins}m` : `${mins}m`);
};
const windowRow = w => `<div class="win"><div class="quota-heading"><span class="lbl">${esc(wl(w.label))} · ${t('remaining')}</span><span class="quota-value" style="color:${color(w.remaining_percent)}">${pct(w.remaining_percent).replace('%','')}<small>%</small></span></div>
<span class="bar" role="meter" aria-label="${esc(wl(w.label))} ${t('remaining')}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.max(0,Math.min(100,w.remaining_percent))}"><i style="width:${Math.max(0,Math.min(100,w.remaining_percent))}%;background:${color(w.remaining_percent)}"></i></span>
<div class="reset-line"><span>${t('resetsIn')}</span><b>${countdown(w.reset_at)}</b></div><span class="reset-at">${fmtFull(w.reset_at)}</span></div>`;
const tagChips = a => a.tags.map(tag => `<span class="tag">${esc(tag)}</span>`).join('');
const actionsMenu = a => `<span class="menu" data-menu><button class="icon-btn link" data-act="menu" data-n="${esc(a.name)}" title="${t('actions')}" aria-label="${t('actions')}" aria-expanded="false">⋯</button><div class="dd">
${!a.is_main?`<button data-act="rename" data-n="${esc(a.name)}" ${a.removal_revision?'':'disabled'}>${t('renameAccount')}</button>`:''}
<button data-act="tags" data-n="${esc(a.name)}">${t('editTags')}</button>
<button data-act="relogin" data-n="${esc(a.name)}">${t('relogin')}</button>
<button data-act="copy" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('copyAuth')}</button>
<button data-act="download" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('downloadAuth')}</button>
<button data-act="copypath" data-n="${esc(a.name)}">${t('copyPath')}</button>
${!a.is_main?`<button class="danger" data-act="remove" data-n="${esc(a.name)}" ${a.removal_revision?'':'disabled'}>${t('deleteAccount')}</button>`:''}
${a.has_refresh_token?`<button data-act="refreshtoken" data-n="${esc(a.name)}">${t('refreshToken')}</button>`:''}</div></span>`;
const metric = (label,value,full=false) => `<div class="metric${full?' full':''}"><dt>${label}</dt><dd>${value}</dd></div>`;
const card = (a, collapsed) => {
  const r = a.reset_credits;
  const rc = r == null ? '—' : `<b>${esc(r)}</b> ${t('times')}`+(a.reset_credits_earliest_expiry?`<small>${t('earliest')} ${fmtFull(a.reset_credits_earliest_expiry)}</small>`:'');
  const past = a.subscription_until && new Date(a.subscription_until)<Date.now();
  const paid = a.plan && a.plan.toLowerCase()!=='free';
  const sub = a.subscription_until ? `<span class="${past?'warning':''}">${fmtFull(a.subscription_until)}</span><small>${past?(paid?t('subRenewed'):t('subEnded')):daysLeft(a.subscription_until)}</small>` : '—';
  const tok = a.access_expires ? `<span class="${a.access_expired?'danger':''}">${fmtFull(a.access_expires)}</span><small>${a.access_expired?t('expired'):daysLeft(a.access_expires)}</small>` : '—';
  const extra = a.extras.filter(x=>x.windows.length).map(x=>`<div class="extra-quota"><div class="extra-label">${esc(x.name)}</div>${x.windows.map(w=>`<div class="extra-row"><span>${esc(wl(w.label))} <b style="color:${color(w.remaining_percent)}">${pct(w.remaining_percent)}</b></span><span class="extra-reset"><b>${countdown(w.reset_at)}</b><span>${fmtFull(w.reset_at)}</span></span></div>`).join('')}</div>`).join('');
  const wins = a.windows.length ? `<div class="windows">${a.windows.map(windowRow).join('')}</div>` : `<div class="quota-placeholder">${a.availability==='checking'?'<span class="spin"></span>':''}${t(a.has_tokens?'quotaUnavailable':'statusSignedOut')}</div>`;
  const source = a.source ? `<span class="source ${a.source==='live'?'live':''}" title="${esc(fmtFull(a.source_at))}">${t(a.source==='live'?'sourceLiveShort':'sourceApiShort')} · ${fmtTime(a.source_at)}${a.stale?' · '+t('cachedShort'):''}</span>` : '';
  const error = a.error ? `<div class="err">${esc(a.stale?t('cached'):statusText(a))}<details><summary>${t('errorDetails')}</summary><p>${esc(a.error)}</p></details></div>` : '';
  const canSwitch = a.has_tokens && !a.access_expired && !a.manual_unavailable;
  const initials = (a.display_name || a.name).slice(0,2).toUpperCase();
  return `<article class="card state-${a.availability}${a.active?' active':''}${collapsed?' compact':''}" data-account="${esc(a.name)}">
<div class="card-head"><div class="avatar" aria-hidden="true">${esc(initials)}</div><div class="identity"><h2 class="title" title="${esc(a.display_name)}">${esc(a.display_name)}</h2><div class="identity-meta"><span class="badge">${esc(a.plan_label)}</span><span class="account-name">${esc(a.name)}</span>${a.active?`<span class="badge active-pill">${t('inUse')}</span>`:''}</div></div>${actionsMenu(a)}</div>
<div class="status-line"><span class="status ${a.availability}"><span class="dot" style="background:currentColor"></span>${statusText(a)}</span>${source}</div>
${wins}${a.limit_reached?`<div class="reached">${t('limitReached')}${a.limit_reached_detail?' · '+esc(a.limit_reached_detail):''}</div>`:''}${extra?`<div class="extras">${extra}</div>`:''}
<dl class="metrics">${metric(t('resetCredits'),rc)}${metric(t('subscriptionShort'),sub)}${metric(t('tokenShort'),tok)}${metric(t('lastRefresh'),fmtFull(a.last_refresh))}${a.credits_balance!=null?metric(t('creditsBalance'),esc(a.credits_balance)):''}${a.subscription_checked?metric(t('checkedAtLogin'),fmtFull(a.subscription_checked)):''}</dl>
${error}<div class="tags-row">${tagChips(a)}<button class="link tag-add" data-act="tags" data-n="${esc(a.name)}">+ ${t(a.tags.length?'editTags':'addTag')}</button></div>
<div class="card-footer"><span class="fine" title="${esc(a.auth_path)}">${t('subscriptionSnapshot')}</span><button class="link mini" data-act="${collapsed?'expand':'collapse'}" data-n="${esc(a.name)}" aria-label="${t(collapsed?'expandCard':'collapseCard')}">${t(collapsed?'expandCard':'collapseCard')}</button>${attention(a)?`<button class="mini" data-act="relogin" data-n="${esc(a.name)}">${t('reloginShort')}</button>`:a.active?(a.is_main?`<button class="mini" data-act="save" data-n="main">${t('saveAs')}</button>`:''):`<button class="mini" data-act="switch" data-n="${esc(a.name)}" ${canSwitch?'':'disabled'}>${t('switch')}</button>`}</div></article>`;
};
const render = s => {
  const openMenus = new Set([...document.querySelectorAll('.menu.open')].map(el=>el.closest('.card').dataset.account));
  const openErrors = new Set([...document.querySelectorAll('.card details[open]')].map(el=>el.closest('.card').dataset.account));
  const focused = document.activeElement?.closest('#accounts button, #filters button');
  const focusData = focused ? {...focused.dataset} : null;
  STATE = s;
  document.documentElement.lang = LANG==='zh'?'zh-CN':'en';
  document.title = 'Codex Monitor · '+t('dashboardTitle');
  document.getElementById('title').textContent = t('dashboardTitle');
  document.getElementById('subtitle').textContent = t('dashboardSubtitle');
  document.getElementById('last').textContent = s.refreshing ? t('refreshing') : t('updated')+' '+(s.last_refresh?ago(s.last_refresh):'—');
  document.getElementById('refreshLabel').textContent = t('refreshNow');
  document.getElementById('btnRefresh').disabled = s.refreshing;
  document.getElementById('btnAdd').textContent = '+ '+t('addAccount');
  document.getElementById('btnLog').textContent = t('log');
  document.getElementById('foot').textContent = t('accountsCount').replace('{n}',s.accounts.length)+' · '+s.accounts_dir;
  const errorEl = document.getElementById('annotationError');
  errorEl.textContent = s.annotation_error || ''; errorEl.style.display = s.annotation_error?'block':'none';
  const search = document.getElementById('search'); search.placeholder=t('searchAccounts'); search.setAttribute('aria-label',t('searchAccounts'));
  document.getElementById('lang').setAttribute('aria-label',t('language'));
  const ready=s.accounts.filter(a=>a.availability==='ready').length, limited=s.accounts.filter(a=>a.availability==='limited').length, issues=s.accounts.filter(attention).length;
  const stats=[[s.accounts.length,t('totalAccounts'),''],[ready,t('usableAccounts'),'ready'],[limited,t('limitedAccounts'),''],[issues,t('needsAttention'),'attention']];
  document.getElementById('overview').innerHTML=stats.map(([n,label,cl])=>`<div class="stat ${cl}"><b>${n.toString().padStart(2,'0')}</b><span>${label}</span></div>`).join('');
  const filterDefs=[['all',t('filterAll'),s.accounts.length],['ready',t('filterReady'),ready],['attention',t('filterAttention'),issues],['tagged',t('filterTagged'),s.accounts.filter(a=>a.tags.length).length]];
  document.getElementById('filters').innerHTML=filterDefs.map(([key,label,n])=>`<button class="filter ${key===currentFilter?'selected':''}" data-act="filter" data-filter="${key}" aria-pressed="${key===currentFilter}">${label}<span class="count">${n}</span></button>`).join('');
  const query=search.value.trim().toLowerCase();
  const matches=s.accounts.filter(a=>[a.name,a.display_name,a.plan_label,...a.tags].join(' ').toLowerCase().includes(query)).filter(a=>currentFilter==='all'||(currentFilter==='ready'&&a.availability==='ready')||(currentFilter==='attention'&&attention(a))||(currentFilter==='tagged'&&a.tags.length));
  document.getElementById('sortHint').textContent=t('usableFirst');
  document.getElementById('shownCount').textContent=t('shownCount').replace('{n}',matches.length);
  document.getElementById('btnToggleAll').textContent=t(expandedAll?'collapseAll':'expandAll');
  document.getElementById('accounts').innerHTML=matches.length?matches.map(a=>card(a,!(expandedAll||expanded.has(a.name)))).join(''):`<div class="card empty"><b>${t(s.accounts.length?'noMatches':'emptyTitle')}</b><p>${t(s.accounts.length?'trySearch':'emptyBody')}</p></div>`;
  document.querySelectorAll('.card[data-account]').forEach(el=>{
    if(openMenus.has(el.dataset.account)){el.querySelector('.menu').classList.add('open');el.querySelector('[data-act=menu]').setAttribute('aria-expanded','true');}
    if(openErrors.has(el.dataset.account)){const details=el.querySelector('details');if(details)details.open=true;}
  });
  if(focusData){const next=[...document.querySelectorAll('#accounts button, #filters button')].find(el=>el.dataset.act===focusData.act&&el.dataset.n===focusData.n&&el.dataset.filter===focusData.filter);next?.focus({preventScroll:true});}
  document.getElementById('log').textContent=(s.log||[]).join('\n')||t('noEvents');
};
document.getElementById('search').addEventListener('input',()=>{if(STATE)render(STATE)});
const tick = async () => {try{render(await api('/api/state'));document.getElementById('syncDot').style.background='var(--green)'}catch(e){document.getElementById('last').textContent=e.message;document.getElementById('syncDot').style.background='var(--red)'}};

document.addEventListener('click', async ev => {
  const menuBtn = ev.target.closest('[data-menu] > button');
  document.querySelectorAll('.menu.open').forEach(m => { if (!m.contains(ev.target)) {m.classList.remove('open');m.querySelector('button').setAttribute('aria-expanded','false');} });
  if (menuBtn) { const isOpen=menuBtn.parentElement.classList.toggle('open'); menuBtn.setAttribute('aria-expanded',isOpen); ev.stopPropagation(); return; }
  const b = ev.target.closest('[data-act]'); if (!b) return;
  if (ev.target.closest('.compact') && b.dataset.act !== 'expand' && b.classList.contains('compact')) return;
  const act = b.dataset.act, n = b.dataset.n;
  try {
    if (act === 'filter') { currentFilter=b.dataset.filter; render(STATE); }
    else if (act === 'tags') { openTags(n); }
    else if (act === 'expand') { expanded.add(n); saveExpansion(); render(STATE); }
    else if (act === 'collapse') { if(expandedAll)STATE.accounts.forEach(a=>expanded.add(a.name)); expanded.delete(n); expandedAll = false; saveExpansion(); render(STATE); }
    else if (act === 'toggleall') { expandedAll = !expandedAll; expanded.clear(); saveExpansion(); render(STATE); }
    else if (act === 'switch') { if (confirm(t('switchConfirm').replace('{n}', n))) { await api('/api/switch', 'POST', {name: n}); tick(); } }
    else if (act === 'save') { const name = prompt(t('saveAsPrompt')); if (name) { await api('/api/save', 'POST', {name}); tick(); } }
    else if (act === 'copy') { const r = await api('/api/auth/' + encodeURIComponent(n)); await navigator.clipboard.writeText(r.text); flash(t('copied')); }
    else if (act === 'copypath') { const a = STATE.accounts.find(x => x.name === n); await navigator.clipboard.writeText(a.auth_path); flash(t('copied')); }
    else if (act === 'download') { window.open('/api/auth/' + encodeURIComponent(n) + '?download=1&token=' + encodeURIComponent(TOKEN)); }
    else if (act === 'relogin') { if (confirm(t('reloginConfirm').replace('{n}', n))) openLogin(n, true); }
    else if (act === 'refreshtoken') { if (confirm(t('refreshTokenConfirm'))) { await api('/api/token/refresh', 'POST', {name: n}); tick(); } }
    else if (act === 'rename') { const a=STATE.accounts.find(x=>x.name===n);if(a&&!a.is_main&&a.removal_revision){const newName=prompt(t('renameAccountPrompt').replace('{n}',n),n);if(newName!==null&&newName.trim()&&newName.trim()!==n){const result=await api('/api/rename','POST',{name:n,new_name:newName,revision:a.removal_revision});if(expanded.delete(n))expanded.add(result.name);saveExpansion();await tick();flash(t('accountRenamed'));}} }
    else if (act === 'remove') { const a=STATE.accounts.find(x=>x.name===n);if(a&&!a.is_main&&a.removal_revision&&confirm(t('deleteAccountConfirm').replace('{n}',n).replace('{path}',a.auth_path))){await api('/api/remove','POST',{name:n,revision:a.removal_revision});expanded.delete(n);saveExpansion();await tick();flash(t('accountDeleted'));} }
  } catch (e) { alert(e.message); }
});
let flashTimer;
const flash = msg => { const el=document.getElementById('toast'); el.textContent=msg; el.classList.add('visible'); clearTimeout(flashTimer); flashTimer=setTimeout(()=>el.classList.remove('visible'),2500); };
document.getElementById('btnRefresh').onclick = async () => { try { await api('/api/refresh','POST'); tick(); } catch(e) { flash(e.message); } };
document.getElementById('btnLog').onclick = () => { const l = document.getElementById('log'); l.style.display = l.style.display === 'none' ? 'block' : 'none'; };
document.getElementById('btnAdd').onclick = () => openLogin(null, false);
const langSel = document.getElementById('lang'); langSel.value = LANG; langSel.onchange = () => { LANG = langSel.value; localStorage.setItem('cm_lang', LANG); if (STATE) render(STATE); };


let tagAccountName=null, draftTags=[];
const tagDialog=document.getElementById('tagDialog');
const renderDraftTags = () => {
  const list=document.getElementById('tagList'); list.replaceChildren();
  draftTags.forEach((tag,i)=>{const chip=document.createElement('span');chip.className='tag editable';const input=document.createElement('input');input.type='text';input.value=tag;input.maxLength=30;input.setAttribute('aria-label',t('editTags'));input.oninput=()=>{draftTags[i]=input.value};input.onkeydown=ev=>{if(ev.key==='Enter'){ev.preventDefault();input.blur()}};const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label',t('removeTag')+' '+tag);remove.onclick=()=>{draftTags.splice(i,1);renderDraftTags()};chip.append(input,remove);list.append(chip)});
};
const addDraftTag = () => {
  const input=document.getElementById('tagInput'); const text=input.value.trim();
  if(!text)return true;
  if([...text].length>30 || draftTags.length>=8){document.getElementById('tagError').textContent=t('tagLimits');return false;}
  if(!draftTags.includes(text))draftTags.push(text);
  input.value='';document.getElementById('tagError').textContent='';renderDraftTags();return true;
};
const openTags = name => {
  const account=STATE.accounts.find(a=>a.name===name);tagAccountName=name;draftTags=[...account.tags];
  document.getElementById('tagTitle').textContent=t('editTags');document.getElementById('tagAccount').textContent=account.display_name;
  document.getElementById('tagHint').textContent=t('tagHint');const input=document.getElementById('tagInput');input.value='';input.placeholder=t('tagPlaceholder');input.setAttribute('aria-label',t('addTag'));
  document.getElementById('tagAddBtn').textContent=t('addTag');document.getElementById('tagUnavailableLabel').textContent=t('markUnavailable');document.getElementById('tagUnavailable').checked=account.manual_unavailable;
  document.getElementById('tagCancel').textContent=t('cancel');document.getElementById('tagSave').textContent=t('saveTags');document.getElementById('tagSave').disabled=false;document.getElementById('tagError').textContent='';renderDraftTags();tagDialog.showModal();input.focus();
};
document.getElementById('tagAddBtn').onclick=addDraftTag;
document.getElementById('tagInput').onkeydown=ev=>{if(ev.key==='Enter'){ev.preventDefault();addDraftTag()}};
document.getElementById('tagCancel').onclick=()=>tagDialog.close();
document.getElementById('tagForm').onsubmit=async ev=>{
  ev.preventDefault();if(!addDraftTag())return;
  if(draftTags.some(tag=>!tag.trim()||[...tag.trim()].length>30)){document.getElementById('tagError').textContent=t('tagLimits');return;}
  const save=document.getElementById('tagSave');save.disabled=true;
  try{await api('/api/annotations','POST',{name:tagAccountName,tags:draftTags,unavailable:document.getElementById('tagUnavailable').checked});tagDialog.close();await tick();flash(t('tagsSaved'))}catch(e){document.getElementById('tagError').textContent=e.message}finally{save.disabled=false}
};

// ---- login modal
const modal = document.getElementById('modal');
let loginCtx = null;
function openLogin(name, relogin){
  loginCtx = {name, relogin, mode: 'browser'};
  modal.classList.add('open');
  document.getElementById('mTitle').textContent = relogin ? t('reloginTitle').replace('{n}', name) : t('addTitle');
  document.getElementById('mCancel').textContent = t('cancel');
  const ok = document.getElementById('mOk'); ok.style.display = ''; ok.textContent = t('start');
  document.getElementById('mBody').innerHTML = relogin ? `<div class="small muted">${t('reloginHint')}</div>` : `<div class="small muted" style="margin-bottom:6px">${t('addHint')}</div><input type="text" id="mName" placeholder="work">`;
  ok.onclick = async () => {
    const n = relogin ? name : (document.getElementById('mName').value || '').trim();
    if (!n) return;
    loginCtx.name=n;
    try { renderLogin(await api('/api/login/start', 'POST', {name: n, mode: loginCtx.mode, relogin})); ok.style.display = 'none'; loginTimer = setInterval(pollLogin, 1000); } catch (e) { alert(e.message); }
  };
}
async function pollLogin(){ try { const st = await api('/api/login/status'); renderLogin(st); if (!['starting','waiting','exchanging'].includes(st.phase)) { clearInterval(loginTimer); loginTimer = null; if (st.phase === 'success') tick(); } } catch (e) {} }
function renderLogin(st){
  const body = document.getElementById('mBody'); const ok = document.getElementById('mOk');
  const priv = STATE && STATE.private_browser;
  if (st.phase === 'waiting' && st.mode === 'browser') {
    body.innerHTML = `<b>${t('browserStep')}</b><div class="small muted" style="margin:4px 0 8px">${t('browserHint')}</div>
      <div class="row">${priv ? `<button class="primary" id="openPriv">${t('openPrivate').replace('{b}', priv)}</button>` : ''}<button id="openDef">${t('openDefault')}</button></div>
      <div class="url" style="margin-top:8px">${esc(st.url)}</div><div class="small muted" style="margin-top:8px"><span class="spin"></span> ${t('waitingBrowser')}</div>
      <div style="margin-top:8px"><button class="link small" id="toDevice">${t('useDevice')}</button></div>`;
    const op = document.getElementById('openPriv'); if (op) op.onclick = () => api('/api/login/open', 'POST', {private: true});
    document.getElementById('openDef').onclick = () => api('/api/login/open', 'POST', {private: false});
    document.getElementById('toDevice').onclick = () => restartLogin('device');
  } else if (st.phase === 'waiting' && st.mode === 'device') {
    body.innerHTML = st.url ? `<div class="small muted">${t('deviceHint')}</div><div class="row" style="margin:8px 0">${priv ? `<button class="primary" id="openPriv">${t('openPrivate').replace('{b}', priv)}</button>` : ''}<button id="openDef">${t('openDefault')}</button></div>
      <div class="url">${esc(st.url)}</div><div style="margin:10px 0"><span class="code" id="devCode">${esc(st.code)}</span> <button class="mini" id="copyCode">${t('copyCode')}</button></div><div class="small muted"><span class="spin"></span> ${t('waitingDevice')}</div>
      <div style="margin-top:8px"><button class="link small" id="toBrowser">${t('useBrowser')}</button></div>` : `<span class="spin"></span> ${t('preparing')}`;
    const op = document.getElementById('openPriv'); if (op) op.onclick = () => { navigator.clipboard.writeText(st.code||''); api('/api/login/open', 'POST', {private: true}); };
    const od = document.getElementById('openDef'); if (od) od.onclick = () => { navigator.clipboard.writeText(st.code||''); api('/api/login/open', 'POST', {private: false}); };
    const cc = document.getElementById('copyCode'); if (cc) cc.onclick = () => navigator.clipboard.writeText(st.code||'');
    const tb = document.getElementById('toBrowser'); if (tb) tb.onclick = () => restartLogin('browser');
  } else if (st.phase === 'exchanging') { body.innerHTML = `<span class="spin"></span> ${t('exchanging')}`; }
  else if (st.phase === 'success') {
    body.innerHTML = `<b style="color:var(--green)">&#10003; ${t('loginSuccess')}</b><div class="small" style="margin-top:4px">${esc(st.email||'')} ${esc(st.plan||'')}</div><div class="small muted" style="margin-top:6px">${t('successHint')}</div>`;
    ok.style.display = ''; ok.textContent = t('makeActive'); ok.onclick = async () => { await api('/api/switch', 'POST', {name: st.name}); closeModal(); tick(); };
  } else if (st.phase === 'failed') {
    body.innerHTML = `<b style="color:var(--red)">${t('loginFailed')}</b><pre class="url" style="white-space:pre-wrap">${esc(st.error||'')}</pre><div class="row"><button id="retryB">${t('retryBrowser')}</button><button id="retryD">${t('retryDevice')}</button></div>`;
    document.getElementById('retryB').onclick = () => restartLogin('browser'); document.getElementById('retryD').onclick = () => restartLogin('device');
  } else if (st.phase === 'cancelled') { body.innerHTML = `<div class="muted">${t('cancelled')}</div>`; }
  else if (st.phase === 'starting') { body.innerHTML = `<span class="spin"></span> ${t('preparing')}`; }
}
async function restartLogin(mode){ loginCtx.mode = mode; try { await api('/api/login/cancel', 'POST'); const n = loginCtx.name; renderLogin(await api('/api/login/start', 'POST', {name: n, mode, relogin: loginCtx.relogin})); if (!loginTimer) loginTimer = setInterval(pollLogin, 1000); } catch (e) { alert(e.message); } }
function closeModal(){ modal.classList.remove('open'); if (loginTimer) { clearInterval(loginTimer); loginTimer = null; } api('/api/login/cancel', 'POST').catch(() => {}); }
document.getElementById('mCancel').onclick = closeModal;

tick(); setInterval(tick, 2000);

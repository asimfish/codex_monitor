const TOKEN = new URLSearchParams(location.search).get('token') || '';
const I18N = {en: __EN__, zh: __ZH__};
// Private-mode browsers may deny storage. Keep this tab usable in that case.
function storage(area) {
  const memory = new Map();
  return {
    getItem(key) { try { return window[area].getItem(key) ?? memory.get(key) ?? null; } catch { return memory.get(key) ?? null; } },
    setItem(key,value) { memory.set(key,String(value)); try { window[area].setItem(key,value); } catch {} },
    removeItem(key) { memory.delete(key); try { window[area].removeItem(key); } catch {} }
  };
}
const prefs = storage('localStorage'), session = storage('sessionStorage');
const URL_LANG = new URLSearchParams(location.search).get('lang');
let LANG = URL_LANG || prefs.getItem('cm_lang') || ((navigator.language||'').toLowerCase().startsWith('zh') ? 'zh' : 'en');
function wl(label){ if (label === 'quota') return t('quota'); if (LANG !== 'zh') return label; if (label === 'weekly') return '每周'; if (label === 'daily') return '每天'; if (label === '5h') return '5 小时'; let m = /^(\d+)d$/.exec(label); if (m) return m[1] + ' 天'; m = /^(\d+)h$/.exec(label); if (m) return m[1] + ' 小时'; m = /^(\d+)m$/.exec(label); if (m) return m[1] + ' 分'; return label; }
const t = k => { const v = I18N[LANG] && I18N[LANG][k]; if (v !== undefined) return v; const e = I18N.en[k]; return e !== undefined ? e : k; };
let expandedAll = prefs.getItem('cm_expand_all') !== '0';
const expanded = new Set();
try { const saved=JSON.parse(prefs.getItem('cm_expanded')||'[]');if(Array.isArray(saved))saved.filter(n=>typeof n==='string').forEach(n=>expanded.add(n)); } catch {}
const saveExpansion = () => { prefs.setItem('cm_expand_all',expandedAll?'1':'0');prefs.setItem('cm_expanded',JSON.stringify([...expanded])); };
if (!['en','zh'].includes(LANG)) LANG='en';
let STATE = null, loginTimer = null, loginFp = '', importText = '', importFileName = '', lastActive = null, toastTimer = null;
const tokenRefreshPending = new Set();
const resetCreditPending = new Set();
const actionPending = new Set();
let statePoll = null;

async function api(path, method='GET', body=null) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const r = await fetch(path, {method, signal:controller.signal, headers:{'X-Token':TOKEN,'Content-Type':'application/json'}, body:body ? JSON.stringify(body) : null}).catch(e => {
      if (e.name === 'TypeError') throw new Error(t('requestConnectionFailed'));
      throw e;
    });
    const j = await r.json().catch(() => null);
    if (!r.ok) {
      const message = t('requestFailed').replace('{status}', r.status);
      const detail = typeof j?.error === 'string' ? j.error.trim() : '';
      throw new Error(detail ? message + ' ' + detail : message);
    }
    if (!j || typeof j !== 'object') throw new Error(t('invalidResponse'));
    return j;
  } catch(e) { if(e.name==='AbortError')throw new Error(t('requestTimeout'));throw e; }
  finally { clearTimeout(timeout); }
}
const fmtTime = iso => iso ? new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '-';
const fmtDT = iso => iso ? new Date(iso).toLocaleString([], {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '—';
const fmtDate = iso => iso ? new Date(iso).toLocaleDateString([], {month:'2-digit', day:'2-digit'}) : '—';
const pct = v => Number.isInteger(v) ? v + '%' : v.toFixed(1) + '%';
const color = r => r > 50 ? 'var(--green)' : r > 20 ? 'var(--orange)' : 'var(--red)';
function daysLeft(iso){ if(!iso) return ''; const s=(new Date(iso)-Date.now())/1000; if(s<=0) return t('expired'); const d=Math.floor(s/86400); return d>=1 ? t('daysLeft').replace('{n}', d) : t('hoursLeft').replace('{n}', Math.max(1, Math.floor(s/3600))); }
function ago(iso){ if(!iso) return ''; const s=Math.floor((Date.now()-new Date(iso))/1000); if(s<60) return t('justNow'); if(s<3600) return t('minAgo').replace('{n}', Math.floor(s/60)); return fmtTime(iso); }
const shellQuote=s => "'"+String(s).split("'").join("'\"'\"'")+"'";
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }


function jwtPayload(tok){
  if (!tok || String(tok).split('.').length < 2) return {};
  let p = String(tok).split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
  while (p.length % 4) p += '=';
  try { return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(p),c=>c.charCodeAt(0)))); } catch { return {}; }
}
function inspectAuth(text){
  const auth = JSON.parse(text.replace(/^\uFEFF/,''));
  if(!auth || typeof auth!=='object' || Array.isArray(auth) || (auth.tokens && (typeof auth.tokens!=='object' || Array.isArray(auth.tokens))))throw new Error(t('importNeeded'));
  if(!((typeof auth.tokens?.access_token==='string' && auth.tokens.access_token.trim()) || (typeof auth.OPENAI_API_KEY==='string' && auth.OPENAI_API_KEY.trim())))throw new Error(t('importNeeded'));
  const idc = jwtPayload((auth.tokens||{}).id_token);
  const acc = jwtPayload((auth.tokens||{}).access_token);
  const claims = idc['https://api.openai.com/auth'] || acc['https://api.openai.com/auth'] || {};
  const prof = acc['https://api.openai.com/profile'] || {};
  return {
    email: idc.email || prof.email || '',
    accountId: (auth.tokens||{}).account_id || claims.chatgpt_account_id || '',
    plan: claims.chatgpt_plan_type || '',
    accessExpired: Number.isFinite(acc.exp) && acc.exp * 1000 <= Date.now(),
  };
}
function suggestName(info, filename){
  const fromEmail = (info.email||'').split('@')[0].replace(/[^A-Za-z0-9._-]+/g,'-').replace(/^[._-]+|[._-]+$/g,'');
  if (fromEmail) return fromEmail;
  const fromFile = String(filename||'').replace(/^auth[-_]?/i,'').replace(/\.json$/i,'').replace(/[^A-Za-z0-9._-]+/g,'-').replace(/^[._-]+|[._-]+$/g,'');
  return fromFile;
}
function flash(msg, action, onAction){
  const box = document.getElementById('toast'); const text = document.getElementById('toastMsg'); const btn = document.getElementById('toastAct');
  text.textContent = msg; btn.style.display = action ? '' : 'none'; btn.textContent = action || '';
  btn.onclick = onAction || null; box.classList.add('open');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('open'), 5000);
}
function showNameError(msg){
  const n = document.getElementById('mName'); const err = document.getElementById('mNameErr');
  if (n) n.focus();
  if (err) { err.textContent = msg; err.style.display = ''; }
  else alert(msg);
}

function positionMenu(menu){
  const dd = menu.querySelector('.dd');
  const anchor = menu.querySelector('button').getBoundingClientRect();
  if (anchor.bottom <= 0 || anchor.top >= innerHeight) { closeMenus(); return; }
  const margin = 8, gap = 4;
  const below = Math.max(0, innerHeight - anchor.bottom - gap - margin);
  const above = Math.max(0, anchor.top - gap - margin);
  const down = below >= dd.scrollHeight + 2 || below >= above;
  dd.style.maxHeight = (down ? below : above) + 'px';
  const size = dd.getBoundingClientRect();
  dd.style.left = Math.max(margin, Math.min(anchor.right - size.width, innerWidth - size.width - margin)) + 'px';
  dd.style.top = (down ? anchor.bottom + gap : Math.max(margin, anchor.top - gap - size.height)) + 'px';
}
function closeMenus(){
  document.querySelectorAll('.menu.open').forEach(menu => {
    menu.classList.remove('open');
    menu.querySelector('button').setAttribute('aria-expanded', 'false');
  });
}
function positionOpenMenus(){ document.querySelectorAll('.menu.open').forEach(positionMenu); }
window.addEventListener('resize', positionOpenMenus);
window.addEventListener('scroll', positionOpenMenus, true);
function authRecovery(a){
  if (!a.access_expired && !/token rejected|session revoked|refresh failed|no access_token/i.test(a.error || '')) return '';
  const pending = tokenRefreshPending.has(a.name);
  return `<div class="auth-recovery">
    ${a.has_refresh_token ? `<button class="mini" data-act="refreshtoken" data-n="${esc(a.name)}" ${pending ? 'disabled' : ''}>${pending ? `<span class="spin"></span> ${t('refreshingToken')}` : t('refreshTokenAction')}</button>` : ''}
    <button class="mini" data-act="relogin" data-n="${esc(a.name)}">${t('relogin')}</button>
  </div>`;
}
async function refreshAccountToken(name){
  if (tokenRefreshPending.has(name) || !confirm(t('refreshTokenConfirm'))) return;
  tokenRefreshPending.add(name);
  if (STATE) render(STATE);
  try {
    await api('/api/token/refresh', 'POST', {name});
    await tick();
    flash(t('tokenRefreshed'));
  } catch (e) {
    showTokenRefreshFailure(name, e.message);
  } finally {
    tokenRefreshPending.delete(name);
    if (STATE) render(STATE);
  }
}
function showTokenRefreshFailure(name, message){
  closeModal();
  document.getElementById('mTitle').textContent = t('tokenRefreshFailed');
  document.getElementById('mBody').innerHTML = `<div class="small muted">${t('tokenRefreshFailedHint')}</div><pre class="url" id="tokenRefreshError" style="white-space:pre-wrap"></pre>`;
  document.getElementById('tokenRefreshError').textContent = message;
  document.getElementById('mCancel').textContent = t('cancel');
  const ok = document.getElementById('mOk');
  ok.style.display = ''; ok.textContent = t('relogin');
  ok.onclick = () => openLogin(name, true);
  showModal();
}
function previousResetId(a){ return a.pending_reset_request_id || session.getItem('cm_reset_request_' + (a.account_id || a.name)); }
function resetCardButton(a){
  const pending = resetCreditPending.has(a.account_id || a.name);
  const retry = !!previousResetId(a);
  const disabled = pending || a.reset_recovery_error || a.access_expired || (!(a.reset_credits > 0) && !retry);
  const hint = a.reset_recovery_error || (a.access_expired ? t('resetLoginRequired') : retry ? t('resetRetryHint') : a.reset_credits > 0 ? t('useResetHint') : t('resetNoCredit'));
  return `<button class="mini" data-act="resetcredit" data-n="${esc(a.name)}" title="${esc(hint)}" ${disabled ? 'disabled' : ''}>${pending ? t('usingResetCard') : retry ? t('retryResetCard') : t('useResetCard')}</button>`;
}
async function useResetCard(name){
  const account = STATE?.accounts.find(a => a.name === name);
  if (!account) return;
  const key = account.account_id || account.name;
  const storageKey = 'cm_reset_request_' + key;
  const previousId = previousResetId(account);
  if (resetCreditPending.has(key) || account.reset_recovery_error || account.access_expired || (!(account.reset_credits > 0) && !previousId)) return;
  if (!confirm(t(previousId ? 'retryResetConfirm' : 'useResetConfirm').replace('{n}', account.display_name || name))) return;
  resetCreditPending.add(key);
  render(STATE);
  try {
    // Keep the operation across reloads after an ambiguous timeout. A retry uses
    // the same backend idempotency key and cannot request another card.
    const requestId = previousId || crypto.randomUUID();
    session.setItem(storageKey, requestId);
    const result = await api('/api/reset/use', 'POST', {name, request_id: requestId, confirmed: true});
    session.removeItem(storageKey);
    await tick();
    const messages = {reset: 'resetUsed', nothing_to_reset: 'resetNothingToReset', no_credit: 'resetNoCredit', already_redeemed: 'resetAlreadyRedeemed'};
    flash(t(result.code === 'reset' && result.refresh_pending ? 'resetUsedPending' : messages[result.code] || 'resetFailed'));
  } catch (e) {
    alert(t('resetFailed').replace('{error}', e.message) + '\n' + t('resetRetryHint'));
  } finally {
    resetCreditPending.delete(key);
    if (STATE) render(STATE);
  }
}

const statusKeys = {ready:'statusReady',checking:'statusChecking',limited:'statusLimited',expired:'statusExpired',error:'statusError',signed_out:'statusSignedOut',manual:'statusManual'};
let currentFilter = prefs.getItem('cm_filter') || 'all';
let sortMode = prefs.getItem('cm_sort') || 'ready';
if(!['all','ready','limited','attention','tagged'].includes(currentFilter))currentFilter='all';
if(!['ready','quota','reset','name'].includes(sortMode))sortMode='ready';
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
<button data-act="refreshone" data-n="${esc(a.name)}">${t('refreshAccount')}</button>
<button data-act="tags" data-n="${esc(a.name)}">${t('editTags')}</button>
<button data-act="relogin" data-n="${esc(a.name)}">${t('relogin')}</button>
<button data-act="copy" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('copyAuth')}</button>
<button data-act="download" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('downloadAuth')}</button>
<button data-act="copypath" data-n="${esc(a.name)}">${t('copyPath')}</button>
${!a.is_main?`<button data-act="copyrun" data-n="${esc(a.name)}">${t('copyRun')}</button><button data-act="copyenv" data-n="${esc(a.name)}">${t('copyEnv')}</button><button class="danger" data-act="remove" data-n="${esc(a.name)}" ${a.removal_revision?'':'disabled'}>${t('deleteAccount')}</button>`:''}
${a.has_refresh_token?`<button data-act="refreshtoken" data-n="${esc(a.name)}" ${tokenRefreshPending.has(a.name)?'disabled':''}>${t('refreshToken')}</button>`:''}</div></span>`;
const metric = (label,value,full=false) => `<div class="metric${full?' full':''}"><dt>${label}</dt><dd>${value}</dd></div>`;
const creditValue = a => {
  if (a.credits_unlimited) return t('creditsUnlimited');
  const raw = a.credits_balance;
  if (raw == null || !['number','string'].includes(typeof raw) || String(raw).trim() === '') return '—';
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) return '—';
  return new Intl.NumberFormat(LANG==='zh'?'zh-CN':'en-US', {minimumFractionDigits:Number.isInteger(value)?0:2, maximumFractionDigits:2}).format(value);
};
const card = (a, collapsed) => {
  const r = a.reset_credits;
  const rc = `<div class="reset-count"><b>${r == null ? '—' : esc(r)}</b><span>${t('resetCredits')}</span></div>`;
  const past = a.subscription_until && new Date(a.subscription_until)<Date.now();
  const paid = a.plan && a.plan.toLowerCase()!=='free';
  const sub = a.subscription_until ? `<span class="${past?'warning':''}">${fmtFull(a.subscription_until)}</span><small>${past?(paid?t('subRenewed'):t('subEnded')):daysLeft(a.subscription_until)}</small>` : '—';
  const tok = a.access_expires ? `<span class="${a.access_expired?'danger':''}">${fmtFull(a.access_expires)}</span><small>${a.access_expired?t('expired'):daysLeft(a.access_expires)}</small>` : '—';
  const wins = a.windows.length ? `<div class="windows">${a.windows.map(windowRow).join('')}</div>` : `<div class="quota-placeholder">${a.availability==='checking'?'<span class="spin"></span>':''}${t(a.has_tokens?'quotaUnavailable':'statusSignedOut')}</div>`;
  const source = a.source ? `<span class="source ${a.source==='live'?'live':''}" title="${esc(fmtFull(a.source_at))}">${t(a.source==='live'?'sourceLiveShort':'sourceApiShort')} · ${fmtTime(a.source_at)}${a.stale?' · '+t('cachedShort'):''}</span>` : '';
  const error = a.error ? `<div class="err">${esc(a.stale?t('cached'):statusText(a))}<details data-detail="error"><summary>${t('errorDetails')}</summary><p>${esc(a.error)}</p></details></div>` : '';
  const canSwitch = a.has_tokens && !a.access_expired && !a.manual_unavailable;
  const initials = (a.display_name || a.name).slice(0,2).toUpperCase();
  return `<article class="card state-${a.availability}${a.active?' active':''}${collapsed?' compact':''}" data-account="${esc(a.name)}">
<div class="card-head"><div class="avatar" aria-hidden="true">${esc(initials)}</div><div class="identity"><h2 class="title" title="${esc(a.display_name)}">${esc(a.display_name)}</h2><div class="identity-meta"><span class="badge">${esc(a.plan_label)}</span><span class="account-name">${esc(a.is_main?t('currentLogin'):a.name)}</span>${a.active?`<span class="badge active-pill">${t('inUse')}</span>`:''}</div></div>${actionsMenu(a)}</div>
<div class="status-line"><span class="status ${a.availability}"><span class="dot" style="background:currentColor"></span>${statusText(a)}</span>${source}</div>
${wins}
<div class="credit-balance"><span>${t('creditsBalance')}</span><b>${esc(creditValue(a))}</b></div>
<div class="reset-credit-line">${rc}${resetCardButton(a)}<span class="reset-expiry">${a.reset_credits_earliest_expiry?t('earliest')+' '+fmtFull(a.reset_credits_earliest_expiry):''}</span></div>
<dl class="metrics">${metric(t('subscriptionShort'),sub)}${metric(t('tokenShort'),tok)}${metric(t('lastRefresh'),fmtFull(a.last_refresh),true)}</dl>
<div class="card-notices">${error}${authRecovery(a)}</div><div class="tags-row">${tagChips(a)}<button class="link tag-add" data-act="tags" data-n="${esc(a.name)}">+ ${t(a.tags.length?'editTags':'addTag')}</button></div>
<div class="card-footer"><span class="fine" title="${esc(a.auth_path)}${a.subscription_checked?' · '+t('checkedAtLogin')+' '+fmtFull(a.subscription_checked):''}">${t('subscriptionSnapshot')}</span><button class="link mini" data-act="${collapsed?'expand':'collapse'}" data-n="${esc(a.name)}" aria-label="${t(collapsed?'expandCard':'collapseCard')}">${t(collapsed?'expandCard':'collapseCard')}</button>${attention(a)?`<button class="mini" data-act="relogin" data-n="${esc(a.name)}">${t('reloginShort')}</button>`:a.active?(a.is_main?`<button class="mini" data-act="save" data-n="main">${t('saveAs')}</button>`:`<button class="mini" disabled>${t('inUse')}</button>`):`<button class="mini" data-act="switch" data-n="${esc(a.name)}" ${canSwitch?'':'disabled'}>${t('switch')}</button>`}</div></article>`;
};
const render = s => {
  const openMenus = new Map([...document.querySelectorAll('.menu.open')].map(el=>[el.closest('.card').dataset.account,el.querySelector('.dd').scrollTop]));
  const openDetails = new Set([...document.querySelectorAll('.card details[open]')].map(el=>el.closest('.card').dataset.account+'|'+el.dataset.detail));
  const focused = document.activeElement?.closest('#accounts button, #filters button, #overview button, #accounts summary');
  const focusData = focused ? {...focused.dataset, account:focused.closest('[data-account]')?.dataset.account, detail:focused.parentElement?.dataset.detail} : null;
  STATE = s;
  document.documentElement.lang = LANG==='zh'?'zh-CN':'en';
  document.title = 'Codex Monitor · '+t('dashboardTitle');
  document.getElementById('eyebrow').textContent = t('workspaceEyebrow');
  document.getElementById('accounts').setAttribute('aria-busy','false');
  document.getElementById('title').textContent = t('dashboardTitle');
  document.getElementById('subtitle').textContent = t('dashboardSubtitle');
  document.getElementById('last').textContent = s.refreshing ? t('refreshing') : t('updated')+' '+(s.last_refresh?ago(s.last_refresh):'—');
  document.getElementById('refreshLabel').textContent = t('refreshNow');
  document.getElementById('btnRefresh').disabled = s.refreshing;
  document.getElementById('btnAdd').textContent = '+ '+t('addAccount');
  document.getElementById('btnSettings').title = t('settings');
  document.getElementById('btnSettings').setAttribute('aria-label',t('settings'));
  document.getElementById('btnLog').textContent = t('log');
  document.getElementById('foot').textContent = t('accountsCount').replace('{n}',s.accounts.length)+' · '+s.accounts_dir;
  const errorEl = document.getElementById('annotationError');
  errorEl.textContent = s.annotation_error || ''; errorEl.style.display = s.annotation_error?'block':'none';
  const theme = document.getElementById('theme');
  theme.innerHTML=['auto','light','dark'].map(key=>`<option value="${key}">${t('theme'+key)}</option>`).join('');theme.value=prefs.getItem('cm_theme')||'auto';theme.setAttribute('aria-label',t('theme'));
  const sort = document.getElementById('sort');
  sort.innerHTML=['ready','quota','reset','name'].map(key=>`<option value="${key}">${t('sort'+key)}</option>`).join('');sort.value=sortMode;sort.setAttribute('aria-label',t('sortAccounts'));
  document.getElementById('retryConnection').textContent=t('retry');
  const search = document.getElementById('search'); search.placeholder=t('searchAccounts'); search.setAttribute('aria-label',t('searchAccounts'));
  document.getElementById('lang').setAttribute('aria-label',t('language'));
  const ready=s.accounts.filter(a=>a.availability==='ready').length, limited=s.accounts.filter(a=>a.availability==='limited').length, issues=s.accounts.filter(attention).length;
  const stats=[[s.accounts.length,t('totalAccounts'),'all'],[ready,t('usableAccounts'),'ready'],[limited,t('limitedAccounts'),'limited'],[issues,t('needsAttention'),'attention']];
  document.getElementById('overview').innerHTML=stats.map(([n,label,cl])=>`<button class="stat ${cl} ${currentFilter===cl?'selected':''}" data-act="filter" data-filter="${cl}" aria-pressed="${currentFilter===cl}"><b>${n}</b><span>${label}</span></button>`).join('');
  const filterDefs=[['all',t('filterAll'),s.accounts.length],['ready',t('filterReady'),ready],['limited',t('filterLimited'),limited],['attention',t('filterAttention'),issues],['tagged',t('filterTagged'),s.accounts.filter(a=>a.tags.length).length]];
  document.getElementById('filters').innerHTML=filterDefs.map(([key,label,n])=>`<button class="filter ${key===currentFilter?'selected':''}" data-act="filter" data-filter="${key}" aria-pressed="${key===currentFilter}">${label}<span class="count">${n}</span></button>`).join('');
  const query=search.value.trim().toLowerCase();
  const matches=s.accounts.filter(a=>[a.name,a.display_name,a.plan_label,...a.tags].join(' ').toLowerCase().includes(query)).filter(a=>currentFilter==='all'||(currentFilter==='ready'&&a.availability==='ready')||(currentFilter==='attention'&&attention(a))||(currentFilter==='limited'&&a.availability==='limited')||(currentFilter==='tagged'&&a.tags.length));
  const nextReset=a=>Math.min(...(a.windows||[]).map(w=>w.reset_at?Date.parse(w.reset_at):Infinity));
  if(sortMode!=='ready')matches.sort((a,b)=>sortMode==='name'?a.display_name.localeCompare(b.display_name):sortMode==='quota'?(b.tightest_remaining??-1)-(a.tightest_remaining??-1):nextReset(a)-nextReset(b));
  document.getElementById('sortHint').textContent=t(sortMode==='ready'?'usableFirst':'sort'+sortMode);
  document.getElementById('clearSearch').hidden=!search.value;document.getElementById('clearSearch').setAttribute('aria-label',t('clearSearch'));
  document.getElementById('shownCount').textContent=t('shownCount').replace('{n}',matches.length);
  document.getElementById('btnToggleAll').textContent=t(expandedAll?'collapseAll':'expandAll');
  document.getElementById('accounts').innerHTML=matches.length?matches.map(a=>card(a,!(expandedAll||expanded.has(a.name)))).join(''):`<div class="card empty"><b>${t(s.accounts.length?'noMatches':'emptyTitle')}</b><p>${t(s.accounts.length?'trySearch':'emptyBody')}</p><button class="primary" data-act="${s.accounts.length?'clearfilters':'add'}">${t(s.accounts.length?'clearFilters':'addAccount')}</button></div>`;
  document.querySelectorAll('.card[data-account]').forEach(el=>{
    if(openMenus.has(el.dataset.account)){el.querySelector('.menu').classList.add('open');el.querySelector('[data-act=menu]').setAttribute('aria-expanded','true');positionMenu(el.querySelector('.menu'));el.querySelector('.dd').scrollTop=openMenus.get(el.dataset.account);}
    el.querySelectorAll('details').forEach(details=>{details.open=openDetails.has(el.dataset.account+'|'+details.dataset.detail)});
  });
  document.querySelectorAll('[data-act][data-n]').forEach(el=>{if(actionPending.has(el.dataset.act+'|'+el.dataset.n))el.disabled=true});
  if(focusData){const next=[...document.querySelectorAll('#accounts button, #filters button, #overview button, #accounts summary')].find(el=>el.dataset.act===focusData.act&&el.dataset.n===focusData.n&&el.dataset.filter===focusData.filter&&el.closest('[data-account]')?.dataset.account===focusData.account&&(el.parentElement?.dataset.detail===focusData.detail));next?.focus({preventScroll:true});}
  document.getElementById('log').textContent=(s.log||[]).join('\n')||t('noEvents');
};
document.getElementById('search').addEventListener('input',()=>{if(STATE)render(STATE)});
const tick = () => {
  if(statePoll)return statePoll;
  statePoll = pollState().finally(()=>{statePoll=null});
  return statePoll;
};
async function pollState(){
  try {
    const state = await api('/api/state');
    render(state);
    document.getElementById('connectionBanner').hidden=true;
    document.getElementById('syncDot').style.background='var(--green)';
  } catch(e) {
    document.getElementById('last').textContent=t('disconnected');
    document.getElementById('connectionMessage').textContent=t(STATE?'connectionLost':'connectionFailed')+' '+e.message;
    document.getElementById('retryConnection').textContent=t('retry');
    document.getElementById('connectionBanner').hidden=false;
    document.getElementById('syncDot').style.background='var(--red)';
    document.getElementById('accounts').setAttribute('aria-busy','false');
    if(!STATE)document.getElementById('accounts').replaceChildren();
  }
}
function applyTheme(value){if(['light','dark'].includes(value))document.documentElement.dataset.theme=value;else delete document.documentElement.dataset.theme;}
applyTheme(prefs.getItem('cm_theme'));
document.getElementById('theme').onchange=ev=>{prefs.setItem('cm_theme',ev.target.value);applyTheme(ev.target.value)};
document.getElementById('sort').onchange=ev=>{sortMode=ev.target.value;prefs.setItem('cm_sort',sortMode);if(STATE)render(STATE)};
document.getElementById('clearSearch').onclick=()=>{document.getElementById('search').value='';if(STATE)render(STATE);document.getElementById('search').focus()};
document.getElementById('retryConnection').onclick=()=>tick();

document.addEventListener('click', async ev => {
  const menuBtn = ev.target.closest('[data-menu] > button');
  document.querySelectorAll('.menu.open').forEach(m => { if (!m.contains(ev.target)) {m.classList.remove('open');m.querySelector('button').setAttribute('aria-expanded','false');} });
  if (menuBtn) { const isOpen=menuBtn.parentElement.classList.toggle('open'); menuBtn.setAttribute('aria-expanded',isOpen); if(isOpen)positionMenu(menuBtn.parentElement); ev.stopPropagation(); return; }
  const b = ev.target.closest('[data-act]'); if (!b || b.disabled) return;
  if (ev.target.closest('.compact') && b.dataset.act !== 'expand' && b.classList.contains('compact')) return;
  const act = b.dataset.act, n = b.dataset.n;
  const actionKey=act+'|'+n;
  if(actionPending.has(actionKey))return;
  if(['switch','remove','refreshone','copy','download'].includes(act)){actionPending.add(actionKey);b.disabled=true;}
  try {
    if(act==='clearfilters'){currentFilter='all';document.getElementById('search').value='';prefs.setItem('cm_filter','all');render(STATE)}
    else if(act==='add'){await openLogin(null,false)}
    else if (act === 'filter') { currentFilter=b.dataset.filter;prefs.setItem('cm_filter',currentFilter); render(STATE); }
    else if (act === 'tags') { openTags(n); }
    else if (act === 'expand') { expanded.add(n); saveExpansion(); render(STATE); }
    else if (act === 'collapse') { if(expandedAll)STATE.accounts.forEach(a=>expanded.add(a.name)); expanded.delete(n); expandedAll = false; saveExpansion(); render(STATE); }
    else if (act === 'toggleall') { expandedAll = !expandedAll; expanded.clear(); saveExpansion(); render(STATE); }
    else if (act === 'switch') { await switchTo(n); }
    else if (act === 'save') { openSave(); }
    else if (act === 'copy') { const r = await api('/api/auth/' + encodeURIComponent(n)); await navigator.clipboard.writeText(r.text); flash(t('copied')); }
    else if (act === 'copyrun') { await navigator.clipboard.writeText('codex-monitor run '+shellQuote(n));flash(t('copied')); }
    else if (act === 'copyenv') { await navigator.clipboard.writeText('eval \"$(codex-monitor env '+shellQuote(n)+')\"');flash(t('copied')); }
    else if (act === 'copypath') { const a = STATE.accounts.find(x => x.name === n); await navigator.clipboard.writeText(a.auth_path); flash(t('copied')); }
    else if (act === 'download') { window.open('/api/auth/' + encodeURIComponent(n) + '?download=1&token=' + encodeURIComponent(TOKEN)); }
    else if (act === 'relogin') { if (confirm(t('reloginConfirm').replace('{n}', n))) openLogin(n, true); }
    else if (act === 'refreshtoken') { await refreshAccountToken(n); }
    else if (act === 'resetcredit') { await useResetCard(n); }
    else if (act === 'refreshone') { await api('/api/refresh','POST',{name:n}); await tick(); }
    else if (act === 'rename') { const a=STATE.accounts.find(x=>x.name===n);if(a&&!a.is_main&&a.removal_revision){const newName=prompt(t('renameAccountPrompt').replace('{n}',n),n);if(newName!==null&&newName.trim()&&newName.trim()!==n){const result=await api('/api/rename','POST',{name:n,new_name:newName,revision:a.removal_revision});if(expanded.delete(n))expanded.add(result.name);saveExpansion();await tick();flash(t('accountRenamed'));}} }
    else if (act === 'remove') { const a=STATE.accounts.find(x=>x.name===n);if(a&&!a.is_main&&a.removal_revision&&confirm(t('deleteAccountConfirm').replace('{n}',n).replace('{path}',a.auth_path))){await api('/api/remove','POST',{name:n,revision:a.removal_revision});expanded.delete(n);saveExpansion();await tick();flash(t('accountDeleted'));} }
  } catch (e) { alert(e.message); } finally {actionPending.delete(actionKey);if(STATE)render(STATE);}
});
async function switchTo(n){
  const skip = prefs.getItem('cm_skip_switch_confirm') === '1';
  if (!skip && !confirm(t('switchConfirm').replace('{n}', n))) return;
  const r = await api('/api/switch', 'POST', {name: n});
  lastActive = r.undo || null;
  const msg = t('switched').replace('{n}', n) + (r.codex_running ? ' — ' + t('codexRunning') : '');
  flash(msg, lastActive ? t('undo') : '', lastActive ? async () => {
    const back = lastActive;
    const u = await api('/api/switch', 'POST', {name: back});
    flash(t('switched').replace('{n}', back) + (u.codex_running ? ' — ' + t('codexRunning') : ''));
    tick();
  } : null);
  tick();
}
document.getElementById('btnRefresh').onclick = async () => { try { await api('/api/refresh','POST'); tick(); } catch(e) { flash(e.message); } };
document.getElementById('btnLog').onclick = () => { const l = document.getElementById('log'); l.style.display = l.style.display === 'none' ? 'block' : 'none'; };
document.getElementById('btnSettings').onclick = openSettings;
document.getElementById('btnAdd').onclick = () => openLogin(null, false);
const langSel = document.getElementById('lang'); langSel.value = LANG; langSel.onchange = () => { LANG = langSel.value; prefs.setItem('cm_lang', LANG); if (STATE) render(STATE); };


let tagAccountName=null, draftTags=[];
const tagDialog=document.getElementById('tagDialog');
tagDialog.addEventListener('close',()=>requestAnimationFrame(()=>{[...document.querySelectorAll('[data-act=tags]')].find(el=>el.dataset.n===tagAccountName)?.focus({preventScroll:true})}));
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
let returnFocus = null;
function showModal(){
  if(!modal.classList.contains('open'))returnFocus=document.activeElement;
  closeMenus();
  modal.classList.add('open');
  document.querySelectorAll('main,.topbar').forEach(el=>el.inert=true);
  requestAnimationFrame(()=>{if(modal.classList.contains('open')&&!modal.contains(document.activeElement))(modal.querySelector('input:not([type=file]), button:not([disabled])')||modal).focus()});
}
function modalAction(action){return async()=>{
  const button=document.getElementById('mOk');if(button.disabled)return;
  button.disabled=true;
  try {await action();}catch(e){flash(e.message)}finally{button.disabled=false}
};}

let loginCtx = null;
function importPane(relogin){
  return `<div id="addImport" style="${relogin ? '' : 'display:none'}">
      <div class="small muted">${t('importHint')}</div>
      <div class="drop" id="mDrop">${t('dropHint')}</div>
      <input type="file" id="mFile" aria-label="${t('addViaImport')}" accept=".json,application/json">
      <div class="small muted" id="mFileInfo" style="margin-top:6px"></div>
      <button type="button" class="link small" id="mPasteToggle">${t('orPaste')}</button>
      <textarea id="mPaste" aria-label="${t('addViaImport')}" hidden placeholder='{"tokens":...}'></textarea>
      ${relogin ? '' : `<label class="small muted" style="display:flex;align-items:center;gap:6px;margin-top:8px"><input type="checkbox" id="mForce">${t('importOverwrite')}</label>`}
    </div>`;
}
function bindImportFile(){
  const file = document.getElementById('mFile'); const drop = document.getElementById('mDrop');
  const take = f => {
    if (!f) return;
    if(f.size>250000){alert(t('importTooLarge'));return;}
    const context=loginCtx;
    const r = new FileReader();
    r.onload = () => {
      if(loginCtx!==context)return;
      applyImportText(String(r.result || ''), f.name);
      const paste = document.getElementById('mPaste');
      if (paste) paste.value = '';
    };
    r.onerror=()=>flash(t('importNeeded'));
    r.readAsText(f);
  };
  if (file) file.onchange = ev => take(ev.target.files && ev.target.files[0]);
  if (drop) {
    drop.ondragover = ev => { ev.preventDefault(); drop.classList.add('on'); };
    drop.ondragleave = () => drop.classList.remove('on');
    drop.ondrop = ev => { ev.preventDefault(); ev.stopPropagation(); drop.classList.remove('on'); take(ev.dataTransfer.files && ev.dataTransfer.files[0]); };
  }
  const paste = document.getElementById('mPaste');
  const toggle = document.getElementById('mPasteToggle');
  if (toggle && paste) {
    toggle.onclick = () => { paste.hidden = !paste.hidden; if (!paste.hidden) paste.focus(); };
    paste.oninput = () => {
      const text = paste.value.trim();
      importText = text; importFileName = 'paste';
      if (!text) return;
      try {
        const info = inspectAuth(text);
        const nameEl = document.getElementById('mName');
        if (nameEl && !nameEl.value.trim()) nameEl.value = suggestName(info, '');
        const shown = document.getElementById('mFileInfo');
        if (shown) shown.textContent = t('fileLoaded').replace('{n}', info.email || 'auth.json') + (info.accessExpired ? ' ' + t('importExpiredHint') : '');
      } catch {}
    };
  }
}
function applyImportText(text, filename){
  importText = text.trim(); importFileName = filename || '';
  let info = {};
  try { info = inspectAuth(importText); } catch { importText = ''; alert(t('importNeeded')); return; }
  const nameEl = document.getElementById('mName');
  if (nameEl && !nameEl.value.trim()) nameEl.value = suggestName(info, importFileName);
  const shown = document.getElementById('mFileInfo');
  if (shown) shown.textContent = t('fileLoaded').replace('{n}', importFileName || info.email || 'auth.json') + (info.accessExpired ? ' ' + t('importExpiredHint') : '');
}
async function submitImport(name, force){
  if (!name) { showNameError(t('nameRequired')); return; }
  if (!importText) { alert(t('importNeeded')); return; }
  let info = {};
  try { info = inspectAuth(importText); } catch { alert(t('importNeeded')); return; }
  const dup = (STATE?.accounts || []).find(a => info.accountId && a.account_id === info.accountId && a.name !== name);
  if (dup && !confirm(t('duplicateWarn').replace('{n}', dup.name))) return;
  const context=loginCtx;
  const r = await api('/api/import', 'POST', {name, text: importText, force});
  if(loginCtx!==context)return;
  loginCtx.mode = 'import';
  renderLogin({phase: 'success', name: r.name, email: r.email, plan: r.plan, directory: r.directory, duplicate: r.duplicate});
  tick();
}
async function openLogin(name, relogin){
  if (loginTimer) { clearInterval(loginTimer); loginTimer = null; }
  loginFp = '';
  try { await api('/api/login/cancel', 'POST'); } catch {}
  loginCtx = {name, relogin, mode: 'browser'};
  importText = ''; importFileName = '';
  showModal();
  const label=STATE?.accounts.find(a=>a.name===name)?.is_main?t('currentLogin'):name;
  document.getElementById('mTitle').textContent = relogin ? t('reloginTitle').replace('{n}', label) : t('addTitle');
  document.getElementById('mCancel').textContent = t('cancel');
  const ok = document.getElementById('mOk'); ok.disabled=false; ok.style.display = '';
  if (relogin) {
    document.getElementById('mBody').innerHTML = `<div class="small muted">${t('reloginHint')}</div>
      <div class="tabs"><button class="mini on" id="tabBrowser">${t('addViaBrowser')}</button><button class="mini" id="tabImport">${t('addViaImport')}</button></div>
      <div id="addBrowser"><div class="small muted">${t('browserHint')}</div></div>
      ${importPane(true)}`;
  } else {
    document.getElementById('mBody').innerHTML = `<div class="small muted" style="margin-bottom:6px">${t('addHint')}</div>
      <input type="text" id="mName" maxlength="128" aria-label="${t('accountDirectory')}" placeholder="work">
      <div class="tiny" id="mNameErr" style="color:var(--red);display:none;margin:4px 0 0"></div>
      <div class="tabs"><button class="mini on" id="tabBrowser">${t('addViaBrowser')}</button><button class="mini" id="tabImport">${t('addViaImport')}</button></div>
      <div id="addBrowser"><div class="small muted">${t('browserHint')}</div></div>
      ${importPane(false)}`;
  }
  const setTab = (mode) => {
    loginCtx.mode = mode;
    document.getElementById('tabBrowser').classList.toggle('on', mode === 'browser');
    document.getElementById('tabImport').classList.toggle('on', mode === 'import');
    document.getElementById('addBrowser').style.display = mode === 'browser' ? '' : 'none';
    document.getElementById('addImport').style.display = mode === 'import' ? '' : 'none';
    ok.textContent = mode === 'import' ? t('importStart') : t('start');
  };
  document.getElementById('tabBrowser').onclick = () => setTab('browser');
  document.getElementById('tabImport').onclick = () => setTab('import');
  bindImportFile();
  setTab('browser');
  ok.onclick = modalAction(async () => {
    const context=loginCtx;
    const n = relogin ? name : (document.getElementById('mName').value || '').trim();
    if (!n) { showNameError(t('nameRequired')); return; }
    try {
      if (loginCtx.mode === 'import') await submitImport(n, relogin || !!document.getElementById('mForce')?.checked);
      else {
        let st;
        try {
          st = await api('/api/login/start', 'POST', {name: n, mode: 'browser', relogin: !!relogin});
        } catch (e) {
          if (!relogin && /already exists/i.test(e.message) && confirm(t('existsRelogin').replace('{n}', n))) {
            loginCtx.relogin = true; loginCtx.name = n;
            st = await api('/api/login/start', 'POST', {name: n, mode: 'browser', relogin: true});
          } else throw e;
        }
        if(loginCtx!==context)return;
        loginCtx.name = n;
        ok.style.display = 'none';
        renderLogin(st);
        if (['starting','waiting','exchanging'].includes(st.phase)) {
          if (!loginTimer) loginTimer = setInterval(pollLogin, 1000);
          if (st.phase === 'waiting' && st.mode === 'browser') await openAuthUrl(true);
        } else if (st.phase === 'failed') flash(st.error || t('loginFailed'));
      }
    } catch (e) { alert(e.message); }
  });
}
function openSave(){
  loginCtx = {mode: 'save'};
  showModal();
  document.getElementById('mTitle').textContent = t('saveTitle');
  document.getElementById('mCancel').textContent = t('cancel');
  const ok = document.getElementById('mOk'); ok.style.display = ''; ok.textContent = t('saveStart');
  document.getElementById('mBody').innerHTML = `<div class="small muted" style="margin-bottom:6px">${t('saveHint')}</div>
    <input type="text" id="mName" maxlength="128" aria-label="${t('accountDirectory')}" placeholder="main">
    <div class="tiny" id="mNameErr" style="color:var(--red);display:none;margin:4px 0 0"></div>
    <label class="small muted" style="display:flex;align-items:center;gap:6px;margin-top:8px"><input type="checkbox" id="mForce">${t('importOverwrite')}</label>`;
  ok.onclick = modalAction(async () => {
    const n = (document.getElementById('mName').value || '').trim();
    if (!n) { showNameError(t('nameRequired')); return; }
    try { await api('/api/save', 'POST', {name: n, force: !!document.getElementById('mForce').checked}); closeModal(); tick(); }
    catch (e) { alert(e.message); }
  });
}
function autostartLabel(raw){
  const s = String(raw || '');
  if (!s || /not installed/i.test(s)) return t('autostartNone');
  return t('autostartReady');
}
async function openSettings(){
  loginCtx = {mode: 'settings'};
  showModal();
  document.getElementById('mTitle').textContent = t('settings');
  document.getElementById('mCancel').textContent = t('cancel');
  const ok = document.getElementById('mOk'); ok.style.display = ''; ok.textContent = t('saveStart');
  const context=loginCtx;
  document.getElementById('mBody').innerHTML=`<span class="spin"></span> ${t('preparing')}`;ok.disabled=true;
  let auto = {status: ''};
  try { auto = await api('/api/autostart', 'POST', {action: 'status'}); } catch {}
  if(loginCtx!==context)return;ok.disabled=false;
  document.getElementById('mBody').innerHTML = `<label class="small muted" for="mInterval">${t('interval')}</label>
    <input type="number" id="mInterval" min="10" max="600" step="1" value="${STATE && STATE.interval ? STATE.interval : 30}">
    <label class="small muted" style="display:flex;align-items:center;gap:6px;margin-top:12px"><input type="checkbox" id="mSkipSwitch" ${prefs.getItem('cm_skip_switch_confirm')==='1'?'checked':''}>${t('skipSwitchConfirm')}</label>
    <div class="small muted" style="margin-top:12px">${t('syncHint')}</div>
    <button class="mini" id="btnSync" style="margin-top:6px">${t('syncNow')}</button>
    <div class="small muted" style="margin-top:12px">${t('autostart')}: ${esc(autostartLabel(auto.status || auto.message))}</div>
    <div class="row" style="margin-top:8px"><button class="mini" id="autoOn">${t('autostartOn')}</button><button class="mini" id="autoOff">${t('autostartOff')}</button></div>`;
  document.getElementById('autoOn').onclick = async () => { try { const r = await api('/api/autostart', 'POST', {action: 'install'}); flash(r.message || r.status); } catch (e) { alert(e.message); } };
  document.getElementById('autoOff').onclick = async () => { try { const r = await api('/api/autostart', 'POST', {action: 'remove'}); flash(r.message || r.status); } catch (e) { alert(e.message); } };
  document.getElementById('btnSync').onclick = async () => {
    try { const r = await api('/api/sync', 'POST', {}); flash(r.note || t('syncNone')); tick(); }
    catch (e) { alert(e.message); }
  };
  ok.onclick = modalAction(async () => {
    prefs.setItem('cm_skip_switch_confirm', document.getElementById('mSkipSwitch').checked ? '1' : '0');
    const interval = Number(document.getElementById('mInterval').value);
    if (!Number.isInteger(interval)||interval<10||interval>600) { alert(t('intervalInvalid')); return; }
    try { await api('/api/settings', 'POST', {interval}); closeModal(); tick(); }
    catch (e) { alert(e.message); }
  });
}
async function openAuthUrl(priv){
  try {
    const r = await api('/api/login/open', 'POST', {private: !!priv});
    if (!r.browser || r.browser === 'none') alert(t('openFailed'));
    else flash(t('openedBrowser').replace('{b}', r.browser));
  } catch (e) { alert(e.message); }
}
function loginKey(st){
  return [st.phase||'', st.mode||'', st.url||'', st.code||'', st.error||'', st.email||'', st.name||''].join('\0');
}
async function pollLogin(){const context=loginCtx;try { const st = await api('/api/login/status');if(loginCtx!==context||!modal.classList.contains('open'))return; renderLogin(st); if (!['starting','waiting','exchanging'].includes(st.phase)) { clearInterval(loginTimer); loginTimer = null; if (st.phase === 'success') tick(); } } catch (e) {} }
function renderLogin(st){
  const key = loginKey(st);
  if (key === loginFp && (st.phase === 'waiting' || st.phase === 'starting' || st.phase === 'exchanging')) return;
  loginFp = key;
  const body = document.getElementById('mBody'); const ok = document.getElementById('mOk');
  const priv = STATE && STATE.private_browser;
  if (st.phase === 'waiting' && st.mode === 'browser') {
    body.innerHTML = `<b>${t('browserStep')}</b><div class="small muted" style="margin:4px 0 8px">${t('browserHint')}</div>
      <div class="row">${priv ? `<button class="primary" id="openPriv">${t('openPrivate').replace('{b}', priv)}</button>` : ''}<button id="openDef">${t('openDefault')}</button><button class="mini" id="copyUrl">${t('copyUrl')}</button></div>
      <div class="url" style="margin-top:8px">${esc(st.url)}</div><div class="small muted" style="margin-top:8px"><span class="spin"></span> ${t('waitingBrowser')}</div>
      <div style="margin-top:8px"><button class="link small" id="toDevice">${t('useDevice')}</button></div>`;
    const op = document.getElementById('openPriv'); if (op) op.onclick = () => openAuthUrl(true);
    document.getElementById('openDef').onclick = () => openAuthUrl(false);
    document.getElementById('copyUrl').onclick = () => { navigator.clipboard.writeText(st.url||''); flash(t('copied')); };
    document.getElementById('toDevice').onclick = () => restartLogin('device');
  } else if (st.phase === 'waiting' && st.mode === 'device') {
    body.innerHTML = st.url ? `<div class="small muted">${t('deviceHint')}</div><div class="row" style="margin:8px 0">${priv ? `<button class="primary" id="openPriv">${t('openPrivate').replace('{b}', priv)}</button>` : ''}<button id="openDef">${t('openDefault')}</button></div>
      <div class="url">${esc(st.url)}</div><div style="margin:10px 0"><span class="code" id="devCode">${esc(st.code)}</span> <button class="mini" id="copyCode">${t('copyCode')}</button></div><div class="small muted"><span class="spin"></span> ${t('waitingDevice')}</div>
      <div style="margin-top:8px"><button class="link small" id="toBrowser">${t('useBrowser')}</button></div>` : `<span class="spin"></span> ${t('preparing')}`;
    const op = document.getElementById('openPriv'); if (op) op.onclick = () => { navigator.clipboard.writeText(st.code||''); openAuthUrl(true); };
    const od = document.getElementById('openDef'); if (od) od.onclick = () => { navigator.clipboard.writeText(st.code||''); openAuthUrl(false); };
    const cc = document.getElementById('copyCode'); if (cc) cc.onclick = () => navigator.clipboard.writeText(st.code||'');
    const tb = document.getElementById('toBrowser'); if (tb) tb.onclick = () => restartLogin('browser');
  } else if (st.phase === 'exchanging') { body.innerHTML = `<span class="spin"></span> ${t('exchanging')}`; }
  else if (st.phase === 'success') {
    const imported = loginCtx && loginCtx.mode === 'import';
    const hint = imported ? t('importSuccessHint').replace('{dir}', esc(st.directory || ('~/.codex-accounts/' + st.name))) : t('successHint');
    body.innerHTML = `<b style="color:var(--green)">&#10003; ${imported ? t('importSuccess') : t('loginSuccess')}</b><div class="small" style="margin-top:4px">${esc(st.email||'')} ${esc(st.plan||'')}</div><div class="small muted" style="margin-top:6px">${hint}</div>`;
    ok.style.display = ''; ok.textContent = t('makeActive'); ok.onclick = modalAction(async () => { await api('/api/switch', 'POST', {name: st.name}); closeModal(); tick(); });
  } else if (st.phase === 'failed') {
    const busy = /1455|already in use|app-server|device-code/i.test(st.error||'');
    body.innerHTML = `<b style="color:var(--red)">${t('loginFailed')}</b><pre class="url" style="white-space:pre-wrap">${esc(st.error||'')}</pre>
      ${busy ? `<div class="small" style="margin:8px 0">${t('portBusyHint')}</div>` : ''}
      <div class="row">${busy ? `<button class="primary" id="retryD">${t('retryDevice')}</button><button id="retryB">${t('retryBrowser')}</button>` : `<button id="retryB">${t('retryBrowser')}</button><button id="retryD">${t('retryDevice')}</button>`}</div>`;
    document.getElementById('retryB').onclick = () => restartLogin('browser'); document.getElementById('retryD').onclick = () => restartLogin('device');
  } else if (st.phase === 'cancelled') { body.innerHTML = `<div class="muted">${t('cancelled')}</div>`; }
  else if (st.phase === 'starting') { body.innerHTML = `<span class="spin"></span> ${t('preparing')}`; }
}
async function restartLogin(mode){
  if (!loginCtx) { alert(t('loginFailed')); return; }
  const context=loginCtx;
  loginCtx.mode = mode; loginFp = '';
  renderLogin({phase: 'starting'});
  try {
    await api('/api/login/cancel', 'POST');
    if(loginCtx!==context)return;
    const n = loginCtx.name || STATE?.login?.name;
    if (!n) { alert(t('nameRequired')); return; }
    const st = await api('/api/login/start', 'POST', {name: n, mode, relogin: !!loginCtx.relogin});
    if(loginCtx!==context)return;
    renderLogin(st);
    if (['starting','waiting','exchanging'].includes(st.phase)) {
      if (!loginTimer) loginTimer = setInterval(pollLogin, 1000);
      if (st.phase === 'waiting' && mode === 'browser') await openAuthUrl(true);
    } else if (st.phase === 'failed') flash(st.error || t('loginFailed'));
  } catch (e) { alert(e.message); }
}
function closeModal(){ modal.classList.remove('open'); document.querySelectorAll('main,.topbar').forEach(el=>el.inert=false);if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true}); if (loginTimer) { clearInterval(loginTimer); loginTimer = null; } loginFp = ''; if (loginCtx && (loginCtx.mode === 'browser' || loginCtx.mode === 'device')) api('/api/login/cancel', 'POST').catch(() => {}); loginCtx = null; }
document.getElementById('mCancel').onclick = closeModal;
modal.addEventListener('click', ev => { if (ev.target === modal) closeModal(); });
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') closeMenus();
  if (!modal.classList.contains('open')) return;
  if (ev.key === 'Escape') { ev.preventDefault(); closeModal(); }
  if(ev.key==='Tab'){const items=[...modal.querySelectorAll('button,input,select,textarea,a[href]')].filter(el=>!el.disabled&&el.getClientRects().length);if(items.length){const first=items[0],last=items.at(-1);if(ev.shiftKey&&document.activeElement===first){ev.preventDefault();last.focus()}else if(!ev.shiftKey&&document.activeElement===last){ev.preventDefault();first.focus()}}}
  if (ev.key === 'Enter' && ev.target && ev.target.id === 'mName') { ev.preventDefault(); document.getElementById('mOk').click(); }
});
document.addEventListener('dragover', ev => { if (ev.dataTransfer && [...ev.dataTransfer.types].includes('Files')) ev.preventDefault(); });
document.addEventListener('drop', async ev => {
  const f=ev.dataTransfer?.files?.[0];if(!f)return;
  ev.preventDefault();
  if(ev.target.closest('.modal,dialog') || !/\.json$/i.test(f.name))return;
  if(f.size>250000){flash(t('importTooLarge'));return;}
  await openLogin(null,false);
  const context=loginCtx;
  const reader=new FileReader();
  reader.onload=()=>{if(loginCtx!==context)return;document.getElementById('tabImport')?.click();applyImportText(String(reader.result||''),f.name)};
  reader.onerror=()=>flash(t('importNeeded'));reader.readAsText(f);
});
document.addEventListener('keydown',ev=>{
  const menu=ev.target.closest('.menu.open');
  if(menu&&['ArrowDown','ArrowUp','Home','End'].includes(ev.key)){
    const items=[...menu.querySelectorAll('.dd button:not(:disabled)')];if(!items.length)return;
    ev.preventDefault();const index=items.indexOf(document.activeElement);
    const next=ev.key==='Home'?0:ev.key==='End'?items.length-1:(index+(ev.key==='ArrowUp'?-1:1)+items.length)%items.length;
    items[next].focus();
  }
  if(ev.key==='/'&&!ev.target.closest('input,textarea,select')&&!modal.classList.contains('open')&&!tagDialog.open){ev.preventDefault();document.getElementById('search').focus()}
});
tick(); setInterval(tick, 2000);

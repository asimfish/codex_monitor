"""Local web dashboard: `codex-monitor serve`. Binds to 127.0.0.1 only; every API call must
carry the per-run token so other websites open in your browser cannot drive it."""
from __future__ import annotations

import json
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from . import __version__, browsers, paths
from .monitor import Monitor
from .oauth import OAuthError
from .store import StoreError
from .web_i18n import EN, ZH

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codex Monitor</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:rgba(0,0,0,.08);--accent:#0a84ff;--green:#34c759;--orange:#ff9f0a;--red:#ff3b30;--yellow:#ffd60a;--gray:#aeaeb2}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--card:#2c2c2e;--fg:#f2f2f7;--muted:#98989d;--line:rgba(255,255,255,.1)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:440px;margin:0 auto;padding:12px}
header{display:flex;align-items:center;gap:8px;margin-bottom:10px}header h1{font-size:15px;margin:0;flex:1}
.muted{color:var(--muted)}.small{font-size:11px}.tiny{font-size:10px}
button,select{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:4px 9px;cursor:pointer}
button.primary{background:var(--accent);color:#fff;border-color:transparent}button.mini{padding:2px 7px;font-size:11px}button.link{border:none;background:none;color:var(--accent);padding:2px 4px}
.card{background:var(--card);border-radius:14px;padding:12px;margin-bottom:8px;border:1px solid var(--line)}.card.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.row{display:flex;align-items:center;gap:6px}.title{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;flex:none}.badge{font-size:10px;font-weight:700;padding:1px 6px;border-radius:999px;background:var(--line)}
.bar{flex:1;height:8px;border-radius:999px;background:var(--line);overflow:hidden}.bar i{display:block;height:100%;border-radius:999px}
.win{margin-top:8px}.win .row{gap:8px}.win .lbl{width:44px;color:var(--muted);font-size:12px}.win .pct{width:70px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;margin-top:8px}.grid .k{font-size:10px;color:var(--muted)}.grid .v{font-size:12px;font-variant-numeric:tabular-nums}
.err{color:var(--orange);font-size:11px;margin-top:6px}.reached{color:var(--red);font-weight:600;font-size:11px;margin-top:6px}
.chip{font-size:10px;padding:1px 6px;border-radius:999px;background:var(--line);margin-right:4px}
.compact{display:flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 10px;margin-bottom:6px;cursor:pointer}
.menu{position:relative}.menu .dd{display:none;position:absolute;right:0;top:100%;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px;z-index:5;min-width:200px;box-shadow:0 8px 24px rgba(0,0,0,.15)}
.menu.open .dd{display:block}.dd button{display:block;width:100%;text-align:left;border:none;background:none;padding:6px 8px;border-radius:6px}.dd button:hover{background:var(--line)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;padding:16px}.modal.open{display:flex}
.modal .box{background:var(--card);border-radius:14px;padding:16px;width:100%;max-width:420px}.modal h2{font-size:15px;margin:0 0 10px}
input[type=text]{font:inherit;width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.code{font:700 24px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:1px;padding:6px 12px;background:var(--line);border-radius:10px;display:inline-block}
.url{font:11px ui-monospace,Menlo,Consolas,monospace;word-break:break-all;color:var(--muted)}
footer{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:11px;margin-top:6px}
pre.log{font:10px ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;background:var(--card);border-radius:10px;padding:8px;border:1px solid var(--line);max-height:160px;overflow:auto}
.spin{display:inline-block;width:10px;height:10px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:s .8s linear infinite;vertical-align:middle}@keyframes s{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1 id="title">Codex Monitor</h1>
  <span id="last" class="muted small"></span>
  <button class="mini" id="btnRefresh" title="">&#8635;</button>
  <select id="lang" class="mini"><option value="en">EN</option><option value="zh">中文</option></select>
</header>
<div id="accounts"></div>
<footer>
  <span id="foot"></span>
  <span>
    <button class="link small" id="btnLog"></button>
    <button class="link small" id="btnAdd"></button>
  </span>
</footer>
<pre class="log" id="log" style="display:none"></pre>
</div>

<div class="modal" id="modal"><div class="box">
  <h2 id="mTitle"></h2>
  <div id="mBody"></div>
  <div class="row" style="justify-content:flex-end;margin-top:12px;gap:8px">
    <button id="mCancel"></button>
    <button class="primary" id="mOk"></button>
  </div>
</div></div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const I18N = {en: __EN__, zh: __ZH__};
const URL_LANG = new URLSearchParams(location.search).get('lang');
let LANG = URL_LANG || localStorage.getItem('cm_lang') || ((navigator.language||'').toLowerCase().startsWith('zh') ? 'zh' : 'en');
function wl(label){ if (LANG !== 'zh') return label; if (label === 'weekly') return '每周'; if (label === 'daily') return '每天'; if (label === '5h') return '5 小时'; let m = /^(\d+)d$/.exec(label); if (m) return m[1] + ' 天'; m = /^(\d+)h$/.exec(label); if (m) return m[1] + ' 小时'; m = /^(\d+)m$/.exec(label); if (m) return m[1] + ' 分'; return label; }
const t = k => { const v = I18N[LANG] && I18N[LANG][k]; if (v !== undefined) return v; const e = I18N.en[k]; return e !== undefined ? e : k; };
let expandedAll = localStorage.getItem('cm_expand_all') !== '0';
const expanded = new Set();
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
function statusColor(a){ if(a.error && !a.windows.length) return 'var(--red)'; if(!a.source) return 'var(--gray)'; if(a.stale) return 'var(--yellow)'; return a.tightest_remaining==null ? 'var(--green)' : color(a.tightest_remaining); }

function windowRow(w){
  return `<div class="win"><div class="row"><span class="lbl">${esc(wl(w.label))}</span><span class="bar"><i style="width:${Math.max(0,Math.min(100,w.remaining_percent))}%;background:${color(w.remaining_percent)}"></i></span><span class="pct">${t('remaining')} ${pct(w.remaining_percent)}</span></div>
  <div class="tiny muted" style="margin-left:52px">${t('resets')} ${fmtDT(w.reset_at)} · ${esc(w.reset_in)} ${t('later')}</div></div>`;
}
function actionsMenu(a){
  return `<span class="menu" data-menu><button class="mini" title="${t('actions')}">&#10697;</button><div class="dd">
    <button data-act="copy" data-n="${esc(a.name)}">${t('copyAuth')}</button>
    <button data-act="download" data-n="${esc(a.name)}">${t('downloadAuth')}</button>
    <button data-act="copypath" data-n="${esc(a.name)}">${t('copyPath')}</button>
    <button data-act="relogin" data-n="${esc(a.name)}">${t('relogin')}</button>
    ${a.has_refresh_token ? `<button data-act="refreshtoken" data-n="${esc(a.name)}">${t('refreshToken')}</button>` : ''}
  </div></span>`;
}
function card(a, collapsible){
  const plan = `<span class="badge">${esc(a.plan_label)}</span>`;
  const right = a.active ? `<span class="small" style="color:var(--accent)">${t('inUse')}</span>${a.is_main ? `<button class="link mini" data-act="save" data-n="main">${t('saveAs')}</button>` : ''}`
                         : `<button class="mini" data-act="switch" data-n="${esc(a.name)}">${t('switch')}</button>${collapsible ? `<button class="mini" data-act="collapse" data-n="${esc(a.name)}">&#8963;</button>` : ''}`;
  const wins = a.windows.map(windowRow).join('') || (a.error ? '' : `<div class="muted small" style="margin-top:6px"><span class="spin"></span> ${t('loading')}</div>`);
  const extras = a.extras.filter(x => x.windows.length).map(x => `<div class="tiny muted" style="margin-top:6px">${esc(x.name)} ${x.windows.map(w => `<span class="chip">${esc(wl(w.label))} ${t('left')} ${pct(w.remaining_percent)}</span>`).join('')}</div>`).join('');
  const src = a.source ? `<div class="tiny" style="margin-top:6px;color:${a.source==='live' ? 'var(--green)' : 'var(--muted)'}">${a.source==='live' ? t('sourceLive') : t('sourceApi')} ${fmtTime(a.source_at)}</div>` : '';
  const rc = a.reset_credits == null ? '—' : `${a.reset_credits} ${t('times')}` + (a.reset_credits_earliest_expiry ? ` · ${t('earliest')} ${fmtDate(a.reset_credits_earliest_expiry)}` : '');
  const subPast = a.subscription_until && new Date(a.subscription_until) < Date.now();
  const paid = a.plan && a.plan.toLowerCase() !== 'free';
  const checked = a.subscription_checked ? `<div class="tiny muted">${t('checkedAtLogin')} ${fmtDT(a.subscription_checked)}</div>` : '';
  const sub = !a.subscription_until ? '—' : (subPast ? (paid ? t('subRenewed') : `${fmtDT(a.subscription_until)} (${t('subEnded')})`) : `${fmtDT(a.subscription_until)} (${daysLeft(a.subscription_until)})${checked}`);
  const tok = a.access_expires ? (a.access_expired ? `${fmtDT(a.access_expires)} (${t('expired')})` : `${t('until')} ${fmtDT(a.access_expires)} · ${t('rolling10d')}`) : '—';
  return `<div class="card ${a.active ? 'active' : ''}">
    <div class="row"><span class="dot" style="background:${statusColor(a)}"></span><span class="title" title="${esc(a.auth_path)}">${esc(a.display_name)}</span>${plan}<span style="flex:1"></span>${actionsMenu(a)}${right}</div>
    ${wins}${a.limit_reached ? `<div class="reached">${t('limitReached')}${a.limit_reached_detail ? ' ('+esc(a.limit_reached_detail)+')' : ''}</div>` : ''}${extras}${src}
    <div class="grid"><div><div class="k">${t('resetCredits')}</div><div class="v">${rc}</div></div><div><div class="k">${t('subscription')}</div><div class="v" style="${subPast && !paid ? 'color:var(--orange)' : ''}">${sub}</div></div>
    <div><div class="k">${t('tokenValid')}</div><div class="v" style="${a.access_expired ? 'color:var(--red)' : ''}">${tok}</div></div><div><div class="k">${t('lastRefresh')}</div><div class="v">${a.last_refresh ? new Date(a.last_refresh).toLocaleString() : '—'}</div></div></div>
    ${a.error ? `<div class="err">&#9888; ${esc(a.error)}${a.stale ? ' · ' + t('cached') : ''}</div>` : ''}
  </div>`;
}
function compact(a){
  return `<div class="compact" data-act="expand" data-n="${esc(a.name)}"><span class="dot" style="background:${statusColor(a)}"></span><span class="title" style="font-size:12px;flex:1">${esc(a.display_name)}</span><span class="badge">${esc(a.plan_label)}</span>
    ${a.tightest_remaining != null ? `<b style="color:${color(a.tightest_remaining)}">${t('remaining')} ${pct(a.tightest_remaining)}</b>` : (a.error ? '&#9888;' : '<span class="spin"></span>')}
    ${actionsMenu(a)}<button class="mini" data-act="switch" data-n="${esc(a.name)}">${t('switch')}</button><span class="muted">&#8250;</span></div>`;
}

function render(s){
  STATE = s;
  document.getElementById('title').textContent = t('title');
  document.getElementById('last').textContent = s.last_refresh ? ago(s.last_refresh) : '';
  document.getElementById('btnRefresh').title = t('refreshNow');
  document.getElementById('btnAdd').textContent = '+ ' + t('addAccount');
  document.getElementById('btnLog').textContent = t('log');
  document.getElementById('foot').textContent = t('accountsCount').replace('{n}', s.accounts.length) + ' · ' + s.accounts_dir;
  const el = document.getElementById('accounts');
  if (!s.accounts.length) { el.innerHTML = `<div class="card"><b>${t('emptyTitle')}</b><div class="muted small" style="margin-top:4px">${t('emptyBody')}</div></div>`; return; }
  const active = s.accounts.filter(a => a.active), others = s.accounts.filter(a => !a.active);
  let html = active.map(a => card(a, false)).join('');
  if (others.length) {
    const all = others.every(a => expandedAll || expanded.has(a.name));
    html += `<div class="row small muted" style="margin:4px 4px 6px">${t('otherAccounts').replace('{n}', others.length)}<span style="flex:1"></span><button class="link small" data-act="toggleall">${all ? t('collapseAll') : t('expandAll')}</button></div>`;
    html += others.map(a => (expandedAll || expanded.has(a.name)) ? card(a, true) : compact(a)).join('');
  }
  el.innerHTML = html;
  document.getElementById('log').textContent = (s.log || []).join('\n') || t('noEvents');
  if (s.login && s.login.phase !== 'idle' && document.getElementById('modal').classList.contains('open')) renderLogin(s.login);
}

async function tick(){ try { render(await api('/api/state')); } catch(e) { document.getElementById('last').textContent = e.message; } }

document.addEventListener('click', async ev => {
  const menuBtn = ev.target.closest('[data-menu] > button');
  document.querySelectorAll('.menu.open').forEach(m => { if (!m.contains(ev.target)) m.classList.remove('open'); });
  if (menuBtn) { menuBtn.parentElement.classList.toggle('open'); ev.stopPropagation(); return; }
  const b = ev.target.closest('[data-act]'); if (!b) return;
  if (ev.target.closest('.compact') && b.dataset.act !== 'expand' && b.classList.contains('compact')) return;
  const act = b.dataset.act, n = b.dataset.n;
  try {
    if (act === 'expand') { expanded.add(n); render(STATE); }
    else if (act === 'collapse') { expanded.delete(n); expandedAll = false; localStorage.setItem('cm_expand_all', '0'); render(STATE); }
    else if (act === 'toggleall') { expandedAll = !expandedAll; if (!expandedAll) expanded.clear(); localStorage.setItem('cm_expand_all', expandedAll ? '1' : '0'); render(STATE); }
    else if (act === 'switch') { if (confirm(t('switchConfirm').replace('{n}', n))) { await api('/api/switch', 'POST', {name: n}); tick(); } }
    else if (act === 'save') { const name = prompt(t('saveAsPrompt')); if (name) { await api('/api/save', 'POST', {name}); tick(); } }
    else if (act === 'copy') { const r = await api('/api/auth/' + encodeURIComponent(n)); await navigator.clipboard.writeText(r.text); flash(t('copied')); }
    else if (act === 'copypath') { const a = STATE.accounts.find(x => x.name === n); await navigator.clipboard.writeText(a.auth_path); flash(t('copied')); }
    else if (act === 'download') { window.open('/api/auth/' + encodeURIComponent(n) + '?download=1&token=' + encodeURIComponent(TOKEN)); }
    else if (act === 'relogin') { if (confirm(t('reloginConfirm').replace('{n}', n))) openLogin(n, true); }
    else if (act === 'refreshtoken') { if (confirm(t('refreshTokenConfirm'))) { await api('/api/token/refresh', 'POST', {name: n}); tick(); } }
  } catch (e) { alert(e.message); }
});
function flash(msg){ const el = document.getElementById('last'); const old = el.textContent; el.textContent = msg; setTimeout(() => { el.textContent = old; }, 1500); }
document.getElementById('btnRefresh').onclick = async () => { await api('/api/refresh', 'POST'); setTimeout(tick, 800); };
document.getElementById('btnLog').onclick = () => { const l = document.getElementById('log'); l.style.display = l.style.display === 'none' ? 'block' : 'none'; };
document.getElementById('btnAdd').onclick = () => openLogin(null, false);
const langSel = document.getElementById('lang'); langSel.value = LANG; langSel.onchange = () => { LANG = langSel.value; localStorage.setItem('cm_lang', LANG); if (STATE) render(STATE); };

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
async function restartLogin(mode){ loginCtx.mode = mode; try { await api('/api/login/cancel', 'POST'); const n = loginCtx.relogin ? loginCtx.name : (STATE.login && STATE.login.name) || loginCtx.name; renderLogin(await api('/api/login/start', 'POST', {name: n, mode, relogin: loginCtx.relogin})); if (!loginTimer) loginTimer = setInterval(pollLogin, 1000); } catch (e) { alert(e.message); } }
function closeModal(){ modal.classList.remove('open'); if (loginTimer) { clearInterval(loginTimer); loginTimer = null; } api('/api/login/cancel', 'POST').catch(() => {}); }
document.getElementById('mCancel').onclick = closeModal;

tick(); setInterval(tick, 2000);
</script>
</body>
</html>
"""


def _page() -> bytes:
    html = PAGE.replace("__EN__", json.dumps(EN, ensure_ascii=False)).replace("__ZH__", json.dumps(ZH, ensure_ascii=False))
    return html.encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = f"CodexMonitor/{__version__}"
    monitor: Monitor
    token: str
    quiet: bool = True

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ helpers

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return self.headers.get("X-Token") == self.token or query.get("token", [None])[0] == self.token

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    # ------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            if not self._authorized():
                self._json(403, {"error": "open the dashboard through the URL printed by `codex-monitor serve` (it carries the access token)"})
                return
            body = _page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authorized():
            self._json(403, {"error": "missing or invalid token"})
            return
        if path == "/api/state":
            self._json(200, self.monitor.snapshot())
        elif path == "/api/login/status":
            self.monitor.finish_login_if_done()
            self._json(200, self.monitor.login_status())
        elif path.startswith("/api/auth/"):
            name = urllib.parse.unquote(path[len("/api/auth/"):])
            try:
                text = self.monitor.auth_text(name)
            except (KeyError, OSError):
                self._json(404, {"error": f"unknown account {name!r}"})
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if query.get("download"):
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="auth-{name}.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(200, {"name": name, "text": text})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if not self._authorized():
            self._json(403, {"error": "missing or invalid token"})
            return
        body = self._body()
        m = self.monitor
        try:
            if path == "/api/refresh":
                threading.Thread(target=m.refresh_all, kwargs={"force": True}, daemon=True).start()
                self._json(200, {"ok": True})
            elif path == "/api/switch":
                self._json(200, {"ok": True, "messages": m.switch(str(body.get("name", "")))})
            elif path == "/api/save":
                self._json(200, {"ok": True, "name": m.save_main(str(body.get("name", "")))})
            elif path == "/api/token/refresh":
                self._json(200, {"ok": True, "result": m.refresh_token(str(body.get("name", "")))})
            elif path == "/api/login/start":
                self._json(200, m.start_login(str(body.get("name", "")), str(body.get("mode", "browser")), bool(body.get("relogin"))))
            elif path == "/api/login/open":
                self._json(200, {"ok": True, "browser": m.open_login_url(bool(body.get("private", True)))})
            elif path == "/api/login/cancel":
                m.cancel_login()
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except (StoreError, OAuthError, KeyError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # keep the server alive; surface the message
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def make_server(monitor: Monitor, host: str = "127.0.0.1", port: int = 7860, token: Optional[str] = None,
                quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (DashboardHandler,), {"monitor": monitor, "token": token or secrets.token_urlsafe(24), "quiet": quiet})
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def dashboard_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}/?token={server.RequestHandlerClass.token}"  # type: ignore[attr-defined]


def serve(port: int = 7860, interval: int = 30, open_browser: str = "tab", host: str = "127.0.0.1", quiet: bool = True) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: binding to a non-loopback address exposes auth.json contents to your network", file=sys.stderr)
    monitor = Monitor(interval=interval)
    server = make_server(monitor, host=host, port=port, quiet=quiet)
    url = dashboard_url(server)
    monitor.start_background()
    print(f"Codex Monitor dashboard: {url}", flush=True)
    print(f"accounts: {paths.accounts_dir()}   codex home: {paths.codex_home()}   refresh every {monitor.interval}s", flush=True)
    print("press Ctrl+C to stop", flush=True)
    if open_browser == "app":
        browsers.open_app_window(url)
    elif open_browser == "tab":
        browsers.open_default(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()

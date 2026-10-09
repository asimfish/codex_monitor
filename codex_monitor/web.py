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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23202c3a'/%3E%3Cpath d='m9 9 6 6-6 6m8 2h7' fill='none' stroke='white' stroke-width='2'/%3E%3C/svg%3E">
<title>Codex Monitor</title>
<style>

:root{color-scheme:light;--bg:#f5f7f9;--card:#fff;--fg:#202c3a;--muted:#657489;--line:#e6ebf0;--accent:#0f766e;--accent-soft:#eaf6f2;--green:#12825e;--orange:#a25b10;--red:#c53942;--red-soft:#fff0f1;--yellow:#b78316;--gray:#92a0af;--soft:#f7f9fb;--shadow:0 3px 10px rgba(24,39,55,.025)}
*{box-sizing:border-box}body{margin:0;color:var(--fg);background:var(--bg);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
button,input,select{font:inherit}button,select{cursor:pointer}button{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);border-radius:7px;padding:7px 11px;background:var(--card);color:var(--fg);white-space:nowrap;transition:background .12s}button:hover{background:var(--soft)}button:disabled{cursor:default;opacity:.5}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button.primary:hover{filter:brightness(1.07)}button.mini{padding:4px 9px;font-size:12px}button.link{border-color:transparent;background:transparent;color:var(--muted);padding:4px}button.link:hover{color:var(--accent)}button.icon-btn{font-size:19px;width:28px;height:28px;padding:0}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}input,select{color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:7px;padding:8px 10px}input{min-width:0}input[type=text],input[type=search]{width:100%}
.topbar{border-bottom:1px solid var(--line);background:var(--card)}.topbar-inner{max-width:1680px;margin:auto;padding:15px 28px;display:flex;align-items:center;justify-content:space-between;gap:16px}.brand{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;letter-spacing:-.3px}.brandmark{width:31px;height:31px;display:grid;place-items:center;border-radius:9px;background:var(--fg);color:var(--card);font:700 16px ui-monospace,monospace}.header-actions{display:flex;align-items:center;gap:8px}.header-actions select{padding:6px 8px}
.wrap{max-width:1680px;margin:0 auto;padding:26px 28px}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}.page-heading h1{font-size:25px;font-weight:700;letter-spacing:-.8px;margin:0 0 4px}.page-heading p{margin:0;font-size:13px;color:var(--muted)}.sync-state{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;flex-shrink:0}.dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;flex-shrink:0}
.overview{display:grid;grid-template-columns:repeat(4,1fr);background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:22px}.stat{padding:14px 20px;display:flex;align-items:center;gap:12px;border-right:1px solid var(--line)}.stat:last-child{border:0}.stat b{font-size:25px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2}.stat span{font-size:12px;color:var(--muted)}.stat.ready b{color:var(--green)}.stat.attention b{color:var(--red)}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:15px}.filters{display:flex;align-items:center;gap:4px;flex-wrap:wrap}.filter{border-color:transparent;background:transparent;color:var(--muted);padding:6px 10px}.filter.selected{background:var(--accent-soft);color:var(--accent);font-weight:600}.count{font-size:11px;opacity:.75;min-width:14px}.search-wrap{position:relative;width:270px;flex-shrink:0}.search-wrap:before{content:'⌕';position:absolute;left:11px;top:4px;color:var(--muted);font-size:22px}.search-wrap input{padding-left:33px;font-size:12px}.list-caption{display:flex;justify-content:space-between;gap:12px;margin-bottom:12px;font-size:11px;color:var(--muted)}.view-actions{display:flex;gap:12px;align-items:center}
.account-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:14px;align-items:start}.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:11px;padding:16px;box-shadow:var(--shadow);position:relative}.card.active{border-color:#8ac9b9;box-shadow:inset 0 3px 0 var(--accent)}.card.state-manual{background:var(--soft)}.card-head{display:flex;gap:10px;align-items:flex-start;margin-bottom:12px}.avatar{width:34px;height:34px;background:var(--accent-soft);color:var(--accent);border-radius:10px;display:grid;place-items:center;font-size:12px;font-weight:700;flex-shrink:0}.identity{flex:1;min-width:0}.title{margin:0;font-size:13px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.5}.identity-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:10px;color:var(--muted);margin-top:3px}.badge{font-size:9px;font-weight:600;border-radius:4px;padding:1px 5px;border:1px solid var(--line);color:var(--muted);line-height:1.5}.active-pill{color:var(--accent);background:var(--accent-soft);border:0}.account-name{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.status-line{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;font-size:11px}.status{display:flex;align-items:center;gap:5px}.status.ready{color:var(--green)}.status.checking{color:var(--muted)}.status.limited{color:var(--orange)}.status.error,.status.expired,.status.signed_out,.status.manual{color:var(--red)}.source{font-size:10px;color:var(--muted);white-space:nowrap}.source.live{color:var(--green)}
.windows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.win{background:var(--soft);padding:10px 11px;border-radius:7px;min-width:0}.quota-heading{display:flex;align-items:center;justify-content:space-between;gap:5px}.lbl{color:var(--muted);font-size:11px}.quota-value{font-size:20px;font-weight:650;letter-spacing:-.7px;line-height:1.2;font-variant-numeric:tabular-nums}.quota-value small{font-size:11px;margin-left:1px}.bar{height:5px;background:var(--line);border-radius:5px;overflow:hidden;display:block;margin:8px 0 6px}.bar i{display:block;height:100%;border-radius:5px}.reset-line{font-size:10px;color:var(--muted);display:flex;gap:4px;flex-wrap:wrap}.reset-line b{font-weight:500;color:var(--fg)}.reset-at{display:block;font-size:10px;color:var(--muted);margin-top:2px}.quota-placeholder{padding:18px 12px;border:1px dashed var(--line);border-radius:7px;color:var(--muted);font-size:12px;min-height:92px;display:flex;align-items:center;justify-content:center;gap:8px}.extras{margin-top:10px}.extra-label{font-size:10px;color:var(--muted);margin:8px 0 5px}.windows:has(>.win:only-child){grid-template-columns:1fr}.extra-quota{border-top:1px solid var(--line);padding-top:1px;margin-top:8px}.extra-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:3px 0;font-size:11px}.extra-row>span:first-child{white-space:nowrap;color:var(--muted)}.extra-row b{font-weight:600;margin-left:5px}.extra-reset{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;color:var(--muted);font-size:10px;text-align:right}.extra-reset b{color:var(--fg);font-weight:500;margin:0}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;border-top:1px solid var(--line);padding-top:11px;margin:12px 0 0}.metric{min-width:0}.metric dt{font-size:10px;color:var(--muted);margin-bottom:3px}.metric dd{font-size:11px;margin:0;color:var(--fg);overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.metric dd small{font-size:10px;color:var(--muted);display:block;margin-top:2px}.metric .danger{color:var(--red)}.metric .warning{color:var(--orange)}.metric.full{grid-column:1/-1}.fine{font-size:10px;color:var(--muted)}
.tags-row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:10px;margin-top:12px;min-height:30px}.tag{background:var(--red-soft);color:var(--red);border:1px solid color-mix(in srgb,var(--red) 15%,transparent);border-radius:4px;padding:2px 6px;font-size:10px;max-width:100%;overflow-wrap:anywhere;white-space:normal}.tag.editable{display:flex;gap:6px;align-items:center;padding:4px 8px;font-size:12px;width:100%}.tag.editable input{border:0;background:transparent;color:inherit;flex:1;min-width:0;padding:3px 0;font-size:12px}.tag.editable button{padding:0;border:0;background:transparent;color:inherit;font-size:15px;min-width:18px}.tag-add{font-size:10px!important;padding:2px 5px!important;border:1px dashed var(--line)!important}.card-footer{display:flex;align-items:center;gap:6px;margin-top:10px}.card-footer .fine{flex:1}.card-footer button{font-size:11px}.err{margin-top:10px;border-radius:6px;background:var(--red-soft);padding:9px 10px;color:var(--red);font-size:11px;overflow-wrap:anywhere}.err details summary{cursor:pointer;font-size:10px;margin-top:5px}.err details p{white-space:pre-wrap;margin:6px 0 0;font:10px/1.5 ui-monospace,monospace}.reached{color:var(--orange);font-size:11px;margin-top:8px}.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.muted{color:var(--muted)}.small{font-size:12px}.tiny{font-size:10px}
.menu{position:relative;flex-shrink:0}.menu .dd{display:none;position:absolute;right:0;top:calc(100% + 5px);min-width:208px;border:1px solid var(--line);background:var(--card);padding:5px;border-radius:9px;box-shadow:0 10px 30px rgba(24,39,55,.15);z-index:20}.menu.open{z-index:20}.menu.open .dd{display:block}.dd button{display:flex;width:100%;border:0;text-align:left;justify-content:flex-start;background:transparent;border-radius:5px;padding:8px;font-size:12px}.dd button:hover{background:var(--soft)}.compact .windows,.compact .metrics,.compact .extras{display:none}.compact .status-line{margin-bottom:0}.compact .tags-row{margin-top:10px}.empty{grid-column:1/-1;text-align:center;padding:50px 20px}.empty b{font-size:15px}.empty p{margin:8px 0 0;color:var(--muted)}
footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);padding:14px 0 4px;margin-top:24px;font-size:10px;color:var(--muted)}.foot-path{overflow-wrap:anywhere;min-width:0}pre.log{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;font:10px/1.6 ui-monospace,monospace;white-space:pre-wrap;max-height:220px;overflow:auto}.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);max-width:90%;padding:10px 16px;border-radius:8px;background:var(--fg);color:var(--card);box-shadow:0 8px 25px #0002;font-size:12px;z-index:120;display:none}.toast.visible{display:block}.annotation-error{display:none;margin-bottom:16px}
.modal{position:fixed;inset:0;background:rgba(14,28,43,.4);display:none;align-items:center;justify-content:center;padding:20px;z-index:100;backdrop-filter:blur(3px)}.modal.open{display:flex}.modal .box,dialog{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:14px;padding:24px;width:100%;max-width:480px;max-height:90dvh;overflow:auto;box-shadow:0 24px 65px rgba(14,28,43,.18)}.modal h2,dialog h2{font-size:18px;margin:0 0 15px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}dialog::backdrop{background:rgba(14,28,43,.4);backdrop-filter:blur(3px)}.dialog-account{color:var(--muted);font-size:12px;margin:-8px 0 18px;overflow-wrap:anywhere}.tag-hint{font-size:12px;color:var(--muted);margin:0 0 12px}.tag-input-row{display:flex;gap:8px}.tag-list{display:flex;gap:7px;flex-wrap:wrap;min-height:35px;margin:14px 0}.checkbox-label{display:flex;align-items:center;gap:8px;border-top:1px solid var(--line);padding-top:16px;font-size:12px}.checkbox-label input{accent-color:var(--accent);width:15px;height:15px}.tag-error{font-size:12px;color:var(--red);margin-top:10px}.code{font:700 25px ui-monospace,monospace;letter-spacing:1px;background:var(--soft);padding:8px 12px;border-radius:8px;display:inline-block}.url{font:11px ui-monospace,monospace;word-break:break-all;color:var(--muted)}.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111820;--card:#1a232e;--fg:#e7edf5;--muted:#9aa9ba;--line:#2b3644;--soft:#202b37;--accent:#64c6af;--accent-soft:#213c36;--green:#6bd5a9;--orange:#eabb78;--red:#ff929b;--red-soft:#38252d;--gray:#718397}button.primary{background:#267560;color:#fff;border-color:#267560}.card.active{border-color:#42796a}.card{box-shadow:none}}
@media(min-width:1800px){.account-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:850px){.wrap{padding:20px}.topbar-inner{padding:13px 20px}.account-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:13px}.stat{padding:13px}.stat b{font-size:22px}.toolbar{flex-wrap:wrap}.search-wrap{width:100%;order:-1}}
@media(max-width:650px){.account-grid{grid-template-columns:1fr}.topbar-inner{padding:12px 15px;flex-wrap:wrap}.wrap{padding:18px 15px}.brand{font-size:14px}.header-actions{gap:5px}.page-heading{align-items:flex-start;flex-wrap:wrap}.page-heading h1{font-size:22px}.overview{grid-template-columns:repeat(2,1fr);margin-bottom:16px}.stat:nth-child(2){border-right:0}.stat:nth-child(-n+2){border-bottom:1px solid var(--line)}.list-caption{align-items:flex-start;flex-wrap:wrap}.card{padding:15px}.source{font-size:9px}.modal{padding:12px}.modal .box,dialog{padding:20px;max-width:calc(100% - 24px)}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="topbar"><div class="topbar-inner">
  <div class="brand"><span class="brandmark" aria-hidden="true">&gt;_</span>Codex Monitor</div>
  <div class="header-actions"><button id="btnRefresh">↻ <span id="refreshLabel"></span></button><select id="lang"><option value="en">EN</option><option value="zh">中文</option></select><button class="primary" id="btnAdd"></button></div>
</div></div>
<main class="wrap">
  <div class="page-heading"><div><h1 id="title"></h1><p id="subtitle"></p></div><span class="sync-state"><span class="dot" id="syncDot"></span><span id="last" aria-live="polite"></span></span></div>
  <div class="overview" id="overview"></div>
  <div class="err annotation-error" id="annotationError" role="alert"></div>
  <div class="toolbar"><nav class="filters" id="filters" aria-label="Account filters"></nav><div class="search-wrap"><input type="search" id="search" autocomplete="off"></div></div>
  <div class="list-caption"><span id="sortHint"></span><div class="view-actions"><span id="shownCount"></span><button class="link small" id="btnToggleAll" data-act="toggleall"></button></div></div>
  <div id="accounts" class="account-grid"></div>
  <footer><span class="foot-path" id="foot"></span><button class="link small" id="btnLog"></button></footer>
  <pre class="log" id="log" style="display:none"></pre>
</main>
<div class="toast" id="toast" role="status"></div>
<div class="modal" id="modal" role="dialog" aria-modal="true" aria-labelledby="mTitle"><div class="box">
  <h2 id="mTitle"></h2><div id="mBody"></div>
  <div class="modal-actions"><button id="mCancel"></button><button class="primary" id="mOk"></button></div>
</div></div>
<dialog id="tagDialog" aria-labelledby="tagTitle"><form id="tagForm">
  <h2 id="tagTitle"></h2><p id="tagAccount" class="dialog-account"></p><p id="tagHint" class="tag-hint"></p>
  <div class="tag-input-row"><input type="text" id="tagInput" maxlength="30" autocomplete="off"><button type="button" id="tagAddBtn"></button></div>
  <div id="tagList" class="tag-list"></div>
  <label class="checkbox-label"><input type="checkbox" id="tagUnavailable"><span id="tagUnavailableLabel"></span></label>
  <p id="tagError" class="tag-error" role="alert"></p>
  <div class="modal-actions"><button type="button" id="tagCancel"></button><button class="primary" type="submit" id="tagSave"></button></div>
</form></dialog>
<script>
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
<button data-act="tags" data-n="${esc(a.name)}">${t('editTags')}</button>
<button data-act="relogin" data-n="${esc(a.name)}">${t('relogin')}</button>
<button data-act="copy" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('copyAuth')}</button>
<button data-act="download" data-n="${esc(a.name)}" ${a.has_tokens?'':'disabled'}>${t('downloadAuth')}</button>
<button data-act="copypath" data-n="${esc(a.name)}">${t('copyPath')}</button>
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
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise StoreError("Invalid request length") from error
        if n < 0 or n > 65536:
            raise StoreError("Request body must be at most 64 KiB")
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError as error:
            raise StoreError("Invalid JSON request") from error
        if not isinstance(data, dict):
            raise StoreError("Request body must be a JSON object")
        return data

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
        m = self.monitor
        try:
            body = self._body()
            if path == "/api/refresh":
                threading.Thread(target=m.refresh_all, kwargs={"force": True}, daemon=True).start()
                self._json(200, {"ok": True})
            elif path == "/api/switch":
                self._json(200, {"ok": True, "messages": m.switch(str(body.get("name", "")))})
            elif path == "/api/save":
                self._json(200, {"ok": True, "name": m.save_main(str(body.get("name", "")))})
            elif path == "/api/annotations":
                self._json(200, {"ok": True, "annotations": m.set_annotations(body.get("name"), body.get("tags"), body.get("unavailable"))})
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

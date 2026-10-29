const query=new URLSearchParams(location.search), token=query.get('token')||'';
const words={
  en:{title:'API credit overview',subtitle:'Track your organizations, grants and reported costs in one place.',back:'Codex accounts',add:'Add API account',refresh:'Refresh costs',note:'Grant totals and dates are entered from API Platform billing. Costs sync requires an Admin API key. Estimated balance = entered grant − reported organization costs; it is not an official credit balance. Discounts, other grants and reporting delays can make it differ. Expired grants do not renew automatically here.',label:'Account name',organization:'API organization ID',adminKey:'Admin API key (optional)',keyHint:'Stored only on this computer. Leave blank while editing to keep the saved key. A normal sk-proj key cannot read organization costs.',grantTotal:'Grant total · USD (entered)',interval:'Refresh interval · seconds',periodStart:'Grant starts (local time)',periodEnd:'Grant expires (local time)',periodHint:'Copy the actual grant amount and dates from billing, including any prorated first grant. Renewal follows your subscription cycle; the panel does not claim or renew credits.',cancel:'Cancel',save:'Save account',edit:'Edit',remove:'Remove',confirm:'Remove this API record and its stored Admin key? Codex accounts stay unchanged.',estimate:'Estimated remaining · USD',cost:'Reported costs · USD',total:'Entered grant · USD',last:'Last successful sync',attempt:'Last attempt',next:'Next scheduled sync',expiry:'Grant expiry',until:'Costs through',noKey:'Admin key required for automatic cost sync',waiting:'Not yet synchronized',expired:'Grant expired · update the record after a new grant',future:'Grant has not started',stale:'Previous data · refresh needed',empty:'Add your first API organization',emptyHint:'Use a name and organization ID. You can record the grant now and add an Admin key later.',queued:'Cost refresh requested. Results update automatically.',billing:'Open official billing',benefit:'Official benefit details',organizations:'API organizations',known:'Entered grants (all periods)',unknown:'Unknown',days:'Daily reported costs',noCosts:'No successful cost query yet',failed:'Cannot connect to the local service',manual:'Manual grant record',updated:'Page updated',formTitle:'API account',error:'Request failed',incomplete:'Some amounts are unknown; no combined balance is inferred.'},
  zh:{title:'API 额度总览',subtitle:'集中查看多个组织的赠送额度、费用和到期时间。',back:'Codex 账号',add:'添加 API 账号',refresh:'刷新费用',note:'赠送总额和账期从 API Platform 账单手动录入；费用自动同步需要 Admin API Key。估算剩余额度＝录入赠送总额－组织报告费用，并非官方余额。折扣、其他赠送和统计延迟都会造成差异。到期后不会在此自动生成新额度。',label:'账号名称',organization:'API 组织 ID',adminKey:'Admin API Key（可选）',keyHint:'仅保存在本机。编辑时留空保留原 Key。普通 sk-proj Key 无权查询组织费用。',grantTotal:'赠送总额 · USD（手填）',interval:'自动刷新间隔 · 秒',periodStart:'额度开始时间（本地时区）',periodEnd:'额度到期时间（本地时区）',periodHint:'请照账单填写实际到账金额和起止时间，首次可能按剩余天数折算。续发按订阅账期，此面板不会领取或续发额度。',cancel:'取消',save:'保存账号',edit:'编辑',remove:'移除',confirm:'移除此 API 记录及其保存的 Admin Key？Codex 账号不受影响。',estimate:'估算剩余 · USD',cost:'已报告费用 · USD',total:'录入总额 · USD',last:'最近成功同步',attempt:'最近尝试',next:'下次计划刷新',expiry:'赠送额度到期',until:'费用统计至',noKey:'添加 Admin Key 后可自动查询费用',waiting:'尚未同步',expired:'赠送已到期 · 新额度到账后请更新记录',future:'额度尚未开始',stale:'旧数据 · 需刷新',empty:'添加第一个 API 组织',emptyHint:'填写名称和组织 ID，可先记录赠送额度，之后补充 Admin Key。',queued:'已请求刷新费用，结果会自动更新。',billing:'打开官方账单',benefit:'查看官方额度说明',organizations:'API 组织',known:'已录入赠送总额（含各账期）',unknown:'未知',days:'每日报告费用',noCosts:'尚无成功的费用查询',failed:'无法连接本机服务',manual:'手动额度记录',updated:'页面更新于',formTitle:'API 账号',error:'请求失败',incomplete:'部分金额未知，不推算汇总余额。'}
};
let language=query.get('lang')==='zh'?'zh':(navigator.language.startsWith('zh')?'zh':'en'), state={accounts:[]}, polling=false;
const t=key=>words[language][key], el=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=value=>value===null||value===undefined?'—':Number(value).toLocaleString(language==='zh'?'zh-CN':'en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
const date=value=>value?new Date(value).toLocaleString(language==='zh'?'zh-CN':'en-US'):'—';
const request=async(path,body)=>{
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-Token':token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(15000)});
  const value=await response.json();if(!response.ok)throw new Error(value.error||t('error'));return value;
};
const render=()=>{
  document.documentElement.lang=language==='zh'?'zh-CN':'en';
  for(const [id,key] of Object.entries({apiTitle:'title',apiSubtitle:'subtitle',apiNote:'note',addApi:'add',refreshAll:'refresh',back:'back',billingLink:'billing',benefitLink:'benefit'}))el(id).textContent=t(key);
  el('back').href='/?'+new URLSearchParams({token,lang:language});el('apiLang').value=language;
  el('apiSummary').innerHTML=`<div class="stat"><b>${state.accounts.length}</b><span>${t('organizations')}</span></div><div class="stat"><b>$${money(state.accounts.reduce((sum,a)=>sum+Number(a.grant_total||0),0))}</b><span>${t('known')}</span></div>`;
  el('apiAccounts').innerHTML=state.accounts.length?state.accounts.map(a=>`<article class="api-card" data-id="${a.id}"><header><h2>${esc(a.label)}</h2><div class="small muted">${esc(a.organization)}</div></header>
    <div><span class="small muted">${t('estimate')}</span><div class="api-money ${a.estimated_remaining===null?'unknown':''}">${a.estimated_remaining===null?'—':'$'+money(a.estimated_remaining)}</div><span class="small muted">${t(a.expired?'expired':a.starts_later?'future':!a.has_admin_key?'noKey':a.stale?'stale':'updated')}${!a.stale&&a.last_success?' '+date(a.last_success):''}</span></div>
    <div class="api-values"><div><span class="small muted">${t('total')}</span><b>${a.grant_total===null?t('unknown'):'$'+money(a.grant_total)}</b></div><div><span class="small muted">${t('cost')}</span><b>${a.costs?'$'+money(a.costs.cost_usd):'—'}</b></div></div>
    ${a.error?`<div class="err" role="alert">${esc(a.error)}</div>`:''}
    <div class="small muted api-date">${t('expiry')}：${date(a.period_end)}<br>${t('last')}：${date(a.last_success)}<br>${t('attempt')}：${date(a.last_attempt)}<br>${t('next')}：${date(a.next_refresh)}<br>${t('until')}：${date(a.costs?.queried_until)}</div>
    ${a.costs?`<details><summary class="small">${t('days')}</summary><div class="small">${a.costs.days.map(d=>`<div>${date(new Date(d.start_time*1000).toISOString())} · $${money(d.cost_usd)}</div>`).join('')||t('noCosts')}</div></details>`:''}
    <div class="api-actions"><button data-act="refresh" ${!a.has_admin_key||a.starts_later?'disabled':''}>${t('refresh')}</button><button data-act="edit">${t('edit')}</button><button data-act="remove">${t('remove')}</button></div></article>`).join(''):`<div class="api-card"><h2>${t('empty')}</h2><p class="muted">${t('emptyHint')}</p></div>`;
};
const poll=async()=>{if(polling)return;polling=true;try{state=await request('/api/credits');render();el('apiError').textContent='';el('apiSync').textContent=t('updated')+' '+date(state.now)}catch(error){el('apiError').textContent=t('failed')+' · '+error.message}finally{polling=false}};
const local=value=>{const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16)};
const edit=account=>{
  el('apiForm').reset();el('apiId').value=account?.id||'';el('apiLabel').value=account?.label||'';el('apiOrg').value=account?.organization||'';el('apiGrant').value=account?.grant_total??'';el('apiInterval').value=account?.refresh_interval||300;
  el('apiStart').value=account?local(account.period_start):'';el('apiEnd').value=account?local(account.period_end):'';
  el('apiKey').value='';el('apiFormError').textContent='';el('apiDialogTitle').textContent=t('formTitle');document.querySelectorAll('[data-text]').forEach(node=>node.textContent=t(node.dataset.text));el('apiDialog').showModal();el('apiLabel').focus();
};
el('addApi').onclick=()=>edit(null);el('cancelApi').onclick=()=>el('apiDialog').close();el('apiDialog').addEventListener('close',()=>el('apiKey').value='');
el('apiLang').onchange=event=>{language=event.target.value;render()};
el('refreshAll').onclick=async()=>{el('refreshAll').disabled=true;try{await request('/api/credits/refresh',{});await poll();el('apiSync').textContent=t('queued')}catch(error){el('apiError').textContent=error.message}finally{el('refreshAll').disabled=false}};
el('apiAccounts').onclick=async event=>{
  const button=event.target.closest('[data-act]');if(!button)return;const id=button.closest('[data-id]').dataset.id, account=state.accounts.find(a=>a.id===id);
  if(button.dataset.act==='edit'){edit(account);return}if(button.dataset.act==='remove'&&!confirm(t('confirm')))return;
  button.disabled=true;try{await request('/api/credits/'+(button.dataset.act==='remove'?'remove':'refresh'),{id});await poll()}catch(error){el('apiError').textContent=error.message}finally{button.disabled=false}
};
el('apiForm').onsubmit=async event=>{
  event.preventDefault();const button=el('saveApi');if(button.disabled)return;button.disabled=true;
  const body={label:el('apiLabel').value,organization:el('apiOrg').value.trim(),admin_key:el('apiKey').value.trim(),grant_total:el('apiGrant').value,period_start:new Date(el('apiStart').value).toISOString(),period_end:new Date(el('apiEnd').value).toISOString(),refresh_interval:Number(el('apiInterval').value)};
  if(el('apiId').value)body.id=el('apiId').value;
  try{await request('/api/credits/save',body);el('apiDialog').close();await poll()}catch(error){el('apiFormError').textContent=error.message}finally{button.disabled=false;body.admin_key=''}
};
poll();setInterval(poll,5000);

const query=new URLSearchParams(location.search), token=query.get('token')||'';
const words={
  en:{
    title:'API credit overview',
    subtitle:'Track your organizations, grants and reported costs in one place.',
    back:'Codex accounts',
    add:'Add API account',
    refresh:'Refresh costs',
    note:'Grant totals and dates are entered from API Platform billing. Costs sync requires an Admin API key. Estimated balance = entered grant − reported organization costs; it is not an official credit balance. Discounts, other grants and reporting delays can make it differ. Expired grants do not renew automatically here.',
    label:'Account name (optional)',
    organization:'Organization ID (usually detected automatically)',
    adminKey:'Admin API key',
    keyHint:'Kept only on this computer. Editing: leave blank to keep the saved key. Project keys cannot read costs.',
    grantTotal:'Grant total · USD (entered)',
    interval:'Refresh interval · seconds',
    periodStart:'Grant starts (local time)',
    periodEnd:'Grant expires (local time)',
    periodHint:'Copy the actual grant amount and dates from billing, including any prorated first grant. Renewal follows your subscription cycle; the panel does not claim or renew credits.',
    cancel:'Cancel',
    save:'Save account',
    edit:'Edit',
    remove:'Remove',
    confirm:'Remove this API record and its stored Admin key? Codex accounts stay unchanged.',
    estimate:'Estimated remaining · USD',
    cost:'Reported costs · USD',
    total:'Entered grant · USD',
    last:'Last successful sync',
    attempt:'Last attempt',
    next:'Next scheduled sync',
    expiry:'Grant expiry',
    until:'Costs through',
    noKey:'Admin key required for automatic cost sync',
    waiting:'Not yet synchronized',
    expired:'Grant expired · update the record after a new grant',
    future:'Grant has not started',
    stale:'Previous data · refresh needed',
    empty:'Add your first API organization',
    emptyHint:'Paste an Admin key to start. Grant amount and expiry can be added later.',
    queued:'Cost refresh requested. Results update automatically.',
    billing:'Open official billing',
    benefit:'Official benefit details',
    organizations:'API organizations',
    known:'Entered grants (all periods)',
    unknown:'Unknown',
    days:'Daily reported costs',
    noCosts:'No successful cost query yet',
    failed:'Cannot connect to the local service',
    manual:'Manual grant record',
    updated:'Page updated',
    formTitle:'API account',
    fillGrant:'Add grant details',
    grantMissing:'Grant amount or actual validity dates are not entered. Costs can be synchronized; remaining credits cannot yet be estimated.',
    error:'Request failed',
    incomplete:'Some amounts are unknown; no combined balance is inferred.',
    setupTitle:'Connect in two steps',
    setupSteps:'1. Create an Admin key in the official dashboard. 2. Paste it here and choose Verify & add.',
    createKey:'Open Admin keys in a regular browser ↗',
    ownerHint:'Select the API organization that received your credits. If this page is unavailable, check that you are an organization owner.',
    manualMode:'Save a manual record without an Admin key',
    hasGrant:'Add grant amount and actual validity dates (optional)',
    advanced:'Advanced · organization and refresh interval',
    orgHint:'Leave blank to detect from the successful cost response. For manual records, enter the organization ID.',
    findOrg:'Find organization ID in official settings ↗',
    monthHint:'Start with this calendar month’s reported costs (UTC). Add the actual grant details later to estimate a balance.',
    verifySave:'Verify & add',
    verifyEdit:'Verify & save',
    verifying:'Verifying costs access…',
    monthCost:'This month’s reported costs · USD',
    monthPeriod:'Cost reporting month (UTC)',
    auth401:'The Admin key is invalid or expired. Create a new one in official Admin keys settings.',
    auth403:'Costs access denied (403). Check the Admin key, organization and owner permissions.',
    missingOrg:'Key verified, but no organization ID was returned. Open Advanced and paste the org-… ID from official settings.'
  },
  zh:{
    title:'API 额度总览',
    subtitle:'集中查看多个组织的赠送额度、费用和到期时间。',
    back:'Codex 账号',
    add:'添加 API 账号',
    refresh:'刷新费用',
    note:'赠送总额和账期从 API Platform 账单手动录入；费用自动同步需要 Admin API Key。估算剩余额度＝录入赠送总额－组织报告费用，并非官方余额。折扣、其他赠送和统计延迟都会造成差异。到期后不会在此自动生成新额度。',
    label:'账号名称（可选）',
    organization:'组织 ID（通常自动识别）',
    adminKey:'Admin API Key',
    keyHint:'只保存在本机；编辑时留空保留原 Key。普通项目 Key 无法查询费用。',
    grantTotal:'赠送总额 · USD（手填）',
    interval:'自动刷新间隔 · 秒',
    periodStart:'额度开始时间（本地时区）',
    periodEnd:'额度到期时间（本地时区）',
    periodHint:'请照账单填写实际到账金额和起止时间，首次可能按剩余天数折算。续发按订阅账期，此面板不会领取或续发额度。',
    cancel:'取消',
    save:'保存账号',
    edit:'编辑',
    remove:'移除',
    confirm:'移除此 API 记录及其保存的 Admin Key？Codex 账号不受影响。',
    estimate:'估算剩余 · USD',
    cost:'已报告费用 · USD',
    total:'录入总额 · USD',
    last:'最近成功同步',
    attempt:'最近尝试',
    next:'下次计划刷新',
    expiry:'赠送额度到期',
    until:'费用统计至',
    noKey:'添加 Admin Key 后可自动查询费用',
    waiting:'尚未同步',
    expired:'赠送已到期 · 新额度到账后请更新记录',
    future:'额度尚未开始',
    stale:'旧数据 · 需刷新',
    empty:'添加第一个 API 组织',
    emptyHint:'粘贴 Admin Key 即可开始，赠送金额和到期时间可之后补填。',
    queued:'已请求刷新费用，结果会自动更新。',
    billing:'打开官方账单',
    benefit:'查看官方额度说明',
    organizations:'API 组织',
    known:'已录入赠送总额（含各账期）',
    unknown:'未知',
    days:'每日报告费用',
    noCosts:'尚无成功的费用查询',
    failed:'无法连接本机服务',
    manual:'手动额度记录',
    updated:'页面更新于',
    formTitle:'API 账号',
    fillGrant:'补充额度',
    grantMissing:'尚未填写赠送总额或实际有效期。费用可以同步，剩余额度暂时无法估算。',
    error:'请求失败',
    incomplete:'部分金额未知，不推算汇总余额。',
    setupTitle:'两步完成配置',
    setupSteps:'1. 在官方后台创建 Admin Key。2. 粘贴到这里，点击「验证并添加」。',
    createKey:'用普通浏览器打开 Admin Keys 创建页面 ↗',
    ownerHint:'先切换到领取额度的 API 组织。看不到此页面时，请确认你是该组织的 Owner（所有者）。',
    manualMode:'暂不配置 Admin Key，仅保存手工记录',
    hasGrant:'补充赠送金额和实际有效期（可之后再填）',
    advanced:'高级设置 · 组织和刷新间隔',
    orgHint:'留空时从成功的费用响应自动识别；手工记录需要填写组织 ID。',
    findOrg:'前往官方设置查看组织 ID ↗',
    monthHint:'先同步本自然月费用（UTC），之后补充实际赠送金额和有效期，再估算剩余额度。',
    verifySave:'验证并添加',
    verifyEdit:'验证并保存',
    verifying:'正在验证费用查询权限…',
    monthCost:'本月已报告费用 · USD',
    monthPeriod:'费用统计月份（UTC）',
    auth401:'Admin Key 无效或已过期，请到官方 Admin Keys 页面重新创建。',
    auth403:'费用查询权限不足（403），请确认使用 Admin Key、组织正确且具备 Owner 权限。',
    missingOrg:'Key 已验证，但没有返回组织 ID。请展开高级设置，从官方设置复制 org-… ID 后重试。'
  }
};
const friendly=message=>language==='zh'?(message.includes('invalid or expired')?t('auth401'):message.includes('(403)')?t('auth403'):message.includes('does not match')?'返回的组织 ID 与所选组织不一致。请清空高级设置中的组织 ID 后重试；本次未保存。':message.includes('ID was not returned')?t('missingOrg'):message.includes('regular browser window')?'无法打开普通浏览器，请复制此链接到自己的普通浏览器窗口。':message):message;
let language=query.get('lang')==='zh'?'zh':(navigator.language.startsWith('zh')?'zh':'en'), state={accounts:[]}, polling=false;
const t=key=>words[language][key], el=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=value=>value===null||value===undefined?'—':Number(value).toLocaleString(language==='zh'?'zh-CN':'en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
const date=value=>value?new Date(value).toLocaleString(language==='zh'?'zh-CN':'en-US'):'—';
const request=async(path,body)=>{
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-Token':token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(90000)});
  const value=await response.json();if(!response.ok)throw new Error(friendly(value.error||t('error')));return value;
};
const render=()=>{
  document.documentElement.lang=language==='zh'?'zh-CN':'en';
  for(const [id,key] of Object.entries({apiTitle:'title',apiSubtitle:'subtitle',apiNote:'note',addApi:'add',refreshAll:'refresh',back:'back',billingLink:'billing',benefitLink:'benefit'}))el(id).textContent=t(key);
  el('back').href='/?'+new URLSearchParams({token,lang:language});el('apiLang').value=language;
  el('apiSummary').innerHTML=`<div class="stat"><b>${state.accounts.length}</b><span>${t('organizations')}</span></div><div class="stat"><b>$${money(state.accounts.reduce((sum,a)=>sum+Number(a.grant_total||0),0))}</b><span>${t('known')}</span></div>`;
  el('apiAccounts').innerHTML=state.accounts.length?state.accounts.map(a=>`<article class="api-card" data-id="${a.id}"><header><h2>${esc(a.label)}</h2><div class="small muted">${esc(a.organization)}</div></header>
    <div><span class="small muted">${t('estimate')}</span><div class="api-money ${a.estimated_remaining===null?'unknown':''}">${a.estimated_remaining===null?'—':'$'+money(a.estimated_remaining)}</div><span class="small muted">${t(a.expired?'expired':a.starts_later?'future':!a.has_admin_key?'noKey':a.stale?'stale':'updated')}${!a.stale&&a.last_success?' '+date(a.last_success):''}</span></div>
    <div class="api-values"><div><span class="small muted">${t('total')}</span><b>${a.grant_total===null?t('unknown'):'$'+money(a.grant_total)}</b></div><div><span class="small muted">${t(a.period_mode==='calendar_month'?'monthCost':'cost')}</span><b>${a.costs?'$'+money(a.costs.cost_usd):'—'}</b></div></div>
    ${a.grant_total===null||a.period_mode==='calendar_month'?`<p class="small muted">${t('grantMissing')}</p>`:''}
    ${a.error?`<div class="err" role="alert">${esc(a.error)}</div>`:''}
    <div class="small muted api-date">${t(a.period_mode==='calendar_month'?'monthPeriod':'expiry')}：${a.period_mode==='calendar_month'?new Date(a.period_start).toLocaleDateString(language==='zh'?'zh-CN':'en-US',{year:'numeric',month:'long',timeZone:'UTC'}):date(a.period_end)}<br>${t('last')}：${date(a.last_success)}<br>${t('attempt')}：${date(a.last_attempt)}<br>${t('next')}：${date(a.next_refresh)}<br>${t('until')}：${date(a.costs?.queried_until)}</div>
    ${a.costs?`<details><summary class="small">${t('days')}</summary><div class="small">${a.costs.days.map(d=>`<div>${date(new Date(d.start_time*1000).toISOString())} · $${money(d.cost_usd)}</div>`).join('')||t('noCosts')}</div></details>`:''}
    <div class="api-actions"><button data-act="refresh" ${!a.has_admin_key||a.starts_later?'disabled':''}>${t('refresh')}</button>${a.grant_total===null||a.period_mode==='calendar_month'?`<button data-act="grant">${t('fillGrant')}</button>`:''}<button data-act="edit">${t('edit')}</button><button data-act="remove">${t('remove')}</button></div></article>`).join(''):`<div class="api-card"><h2>${t('empty')}</h2><p class="muted">${t('emptyHint')}</p></div>`;
};
const poll=async()=>{if(polling)return;polling=true;try{state=await request('/api/credits');render();el('apiError').textContent='';el('apiSync').textContent=t('updated')+' '+date(state.now)}catch(error){el('apiError').textContent=t('failed')+' · '+error.message}finally{polling=false}};
const local=value=>{const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16)};
const updateForm=()=>{
  const grant=el('apiHasGrant').checked, manual=el('apiManual').checked;
  el('apiGrantFields').hidden=!grant;el('apiGrantFields').disabled=!grant;el('apiMonthHint').hidden=grant;el('apiKey').required=!manual&&!el('apiId').value;el('apiOrg').required=manual;
  el('saveApi').textContent=t(el('apiKey').value.trim()||(!el('apiId').value&&!manual)?(el('apiId').value?'verifyEdit':'verifySave'):'save');
};
const edit=(account,grant=false)=>{
  el('apiForm').reset();el('apiId').value=account?.id||'';el('apiLabel').value=account?.label||'';el('apiOrg').value=/^org-[A-Za-z0-9_-]{1,120}$/.test(account?.organization||'')?account.organization:'';el('apiGrant').value=account?.grant_total??'';el('apiInterval').value=account?.refresh_interval||300;
  el('apiHasGrant').checked=grant||!!account&&account.period_mode!=='calendar_month';el('apiManual').checked=!!account&&!account.has_admin_key;
  el('apiStart').value=account&&account.period_mode==='custom'?local(account.period_start):'';el('apiEnd').value=account&&account.period_mode==='custom'?local(account.period_end):'';el('apiAdvanced').open=el('apiManual').checked;
  el('apiKey').value='';el('apiFormError').textContent='';el('apiDialogTitle').textContent=t('formTitle');document.querySelectorAll('[data-text]').forEach(node=>node.textContent=t(node.dataset.text));updateForm();el('apiDialog').showModal();el('apiKey').focus();
};
document.querySelectorAll('[data-settings]').forEach(link=>link.addEventListener('click',async event=>{
  event.preventDefault();
  try{await request('/api/credits/open-settings',{page:link.dataset.settings})}
  catch(error){el(el('apiDialog').open?'apiFormError':'apiError').textContent=error.message}
}));
el('apiHasGrant').onchange=updateForm;el('apiKey').oninput=updateForm;el('apiManual').onchange=()=>{if(el('apiManual').checked)el('apiAdvanced').open=true;updateForm()};
el('addApi').onclick=()=>edit(null);el('cancelApi').onclick=()=>el('apiDialog').close();el('apiDialog').addEventListener('close',()=>el('apiKey').value='');el('apiDialog').addEventListener('cancel',event=>{if(el('saveApi').disabled)event.preventDefault()});
el('apiLang').onchange=event=>{language=event.target.value;render()};
el('refreshAll').onclick=async()=>{el('refreshAll').disabled=true;try{await request('/api/credits/refresh',{});await poll();el('apiSync').textContent=t('queued')}catch(error){el('apiError').textContent=error.message}finally{el('refreshAll').disabled=false}};
el('apiAccounts').onclick=async event=>{
  const button=event.target.closest('[data-act]');if(!button)return;const id=button.closest('[data-id]').dataset.id, account=state.accounts.find(a=>a.id===id);
  if(button.dataset.act==='edit'||button.dataset.act==='grant'){edit(account,button.dataset.act==='grant');return}if(button.dataset.act==='remove'&&!confirm(t('confirm')))return;
  button.disabled=true;try{await request('/api/credits/'+(button.dataset.act==='remove'?'remove':'refresh'),{id});await poll()}catch(error){el('apiError').textContent=error.message}finally{button.disabled=false}
};
el('apiForm').onsubmit=async event=>{
  event.preventDefault();const button=el('saveApi');if(button.disabled)return;button.disabled=true;
  const body={label:el('apiLabel').value,organization:el('apiOrg').value.trim(),admin_key:el('apiKey').value.trim(),period_mode:el('apiHasGrant').checked?'custom':'calendar_month',refresh_interval:Number(el('apiInterval').value)};
  try{
    if(el('apiHasGrant').checked){body.grant_total=el('apiGrant').value;body.period_start=new Date(el('apiStart').value).toISOString();body.period_end=new Date(el('apiEnd').value).toISOString()}
    if(el('apiId').value)body.id=el('apiId').value;
    button.textContent=t(body.admin_key?'verifying':'save');el('apiForm').inert=true;el('cancelApi').disabled=true;el('apiFormError').textContent='';
    await request('/api/credits/'+(body.admin_key?'connect':'save'),body);el('apiDialog').close();await poll()
  }catch(error){el('apiFormError').textContent=error.name==='TimeoutError'?t('failed'):error.message;if(error.message.includes('org-')||error.message.includes('组织 ID')||error.message.includes('selected ID'))el('apiAdvanced').open=true}
  finally{button.disabled=false;el('apiForm').inert=false;el('cancelApi').disabled=false;body.admin_key='';updateForm()}
};
poll();setInterval(poll,5000);

// Browser regressions using fabricated accounts only. Requires Playwright:
// PLAYWRIGHT_MODULE=/path/to/playwright-core node tests/test_web_ui.cjs
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = String.raw`
from datetime import timedelta
from codex_monitor import demo
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server
m = Monitor()
states = demo.states()
states[0].usage['credits'] = {'has_credits': True, 'balance': '62500.0000000000'}
for state in states:
    if state.name in ('bob', 'dave'):
        state.ident['access_expires'] = now_utc() - timedelta(days=1)
        state.error = 'access token expired; refresh or re-login'
    if state.name == 'bob':
        state.ident['has_refresh_token'] = False
m.states = {state.name: state for state in states}
m.order = list(m.states)
server = make_server(m, port=0, token='ui-test')
print(server.server_address[1], flush=True)
server.serve_forever()
`;

async function reachable(locator) {
  return locator.evaluate(el => {
    const dropdown = el.closest('.dd');
    if (dropdown) dropdown.scrollTop = el.offsetTop;
    const r = el.getBoundingClientRect();
    return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
  });
}

(async () => {
  const sandbox = mkdtempSync(path.join(tmpdir(), 'codex-monitor-ui-'));
  const server = spawn(process.env.PYTHON || 'python3', ['-u', '-c', fixture], {
    cwd: path.resolve(__dirname, '..'),
    env: { ...process.env, CODEX_MONITOR_DEMO: '1', CODEX_HOME: path.join(sandbox, 'codex'), CODEX_ACCOUNTS_DIR: path.join(sandbox, 'accounts') },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  let browser;
  try {
    const lines = createInterface({ input: server.stdout });
    const startup = new AbortController();
    const timeout = setTimeout(() => startup.abort(), 10000);
    let port;
    try { [port] = await once(lines, 'line', { signal: startup.signal }); }
    finally { clearTimeout(timeout); lines.close(); }
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let acceptDialogs = true;
    page.on('dialog', dialog => acceptDialogs ? dialog.accept() : dialog.dismiss());
    await page.goto(`http://127.0.0.1:${port}/?token=ui-test&lang=en`);
    await page.locator('.card').last().waitFor();

    const failurePage = await browser.newPage();
    failurePage.on('pageerror', error => errors.push(error.message));
    await failurePage.goto(`http://127.0.0.1:${port}/?token=ui-test&lang=zh`);
    await failurePage.locator('[data-account="alice"]').waitFor();
    for (const scenario of [
      { kind: 'http', status: 504, expected: 'HTTP 504' },
      { kind: 'http', status: 403, expected: 'HTTP 403' },
      { kind: 'network', expected: '无法连接仪表盘服务' },
      { kind: 'abort', expected: '请求超时' },
      { kind: 'server', expected: 'fixture quota error' },
    ]) {
      await failurePage.evaluate(scenario => {
        window.originalFetch ||= window.fetch;
        window.fetch = (path, options) => {
          if (path !== '/api/refresh' || options?.method !== 'POST') return window.originalFetch(path, options);
          if (scenario.kind === 'network') return Promise.reject(new TypeError('请求失败'));
          if (scenario.kind === 'abort') return Promise.reject(new DOMException('Aborted', 'AbortError'));
          const body = scenario.kind === 'server' ? JSON.stringify({ error: 'fixture quota error' }) : '';
          // HTTP/2 and synthetic responses can omit statusText and JSON details.
          return Promise.resolve(new Response(body, { status: scenario.status || 400, statusText: '' }));
        };
      }, scenario);
      const account = failurePage.locator('[data-account="alice"]');
      if (!await account.locator('.menu').evaluate(el => el.classList.contains('open'))) {
        await account.locator('[data-act="menu"]').click();
      }
      const dialogPromise = failurePage.waitForEvent('dialog').then(async dialog => {
        const message = dialog.message();
        await dialog.dismiss();
        return message;
      });
      await account.locator('[data-act="refreshone"]').click();
      const message = await dialogPromise;
      await failurePage.waitForFunction(() => !actionPending.size);
      assert(message.includes(scenario.expected), `Refresh error lost its cause: ${JSON.stringify({ scenario, message })}`);
      if (scenario.kind === 'server') assert(message.includes('HTTP 400'), 'Server details must not hide the HTTP status');
      assert(await account.locator('[data-act="refreshone"]').isEnabled(), 'Failed refresh must remain retryable');
    }
    await failurePage.close();
    console.log('PASS: quota-refresh failures preserve HTTP status, distinguish connection failures and timeouts, and retain server details');

    const credit = page.locator('[data-account="alice"] .credit-balance');
    assert.equal(await credit.locator('b').textContent(), '62,500');
    assert.equal(await page.locator('.reached').count(), 0, 'The redundant quota-limit notice should be removed');
    assert.equal(await page.locator('.extras,.extra-quota').count(), 0, 'The special quota section should be removed');
    await page.locator('[data-account="alice"] [data-act="collapse"]').click();
    assert(await credit.isVisible(), 'Credit quota must remain visible on a collapsed card');
    await page.locator('[data-account="alice"] [data-act="expand"]').click();
    const balances = await page.evaluate(() => {
      const original = STATE;
      const values = [];
      for (const [balance, unlimited] of [[0,false],[null,true],[null,false],['<img src=x>',false]]) {
        render({...original, accounts:original.accounts.map(a=>a.name==='alice'?{...a,credits_balance:balance,credits_unlimited:unlimited}:a)});
        values.push(document.querySelector('[data-account="alice"] .credit-balance b').textContent);
      }
      render(original);
      return values;
    });
    assert.deepEqual(balances, ['0','Unlimited','—','—']);
    console.log('PASS: credit quota stays visible, formats server balances, and distinguishes zero, unlimited and unreported values');

    for (const viewport of [{ width: 1100, height: 800 }, { width: 375, height: 667 }, { width: 320, height: 300 }]) {
      await page.setViewportSize(viewport);
      const card = page.locator('[data-account="dave"]');
      const gear = card.locator('[data-menu] > button');
      await gear.click();
      const items = card.locator('.dd button');
      for (let i = 0; i < await items.count(); i++) {
        assert(await reachable(items.nth(i)), `Dropdown item ${i} is clipped or covered at ${viewport.width}x${viewport.height}`);
      }
      const bounds = await card.locator('.dd').boundingBox();
      assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height, 'Dropdown exceeds viewport');
      // A state update must not dismiss the menu while it is being used.
      const scrollTop = await card.locator('.dd').evaluate(el => el.scrollTop);
      const menuBefore = await card.locator('.dd').evaluate(el => ({ width: el.clientWidth, height: el.clientHeight, scrollHeight: el.scrollHeight, left: el.style.left, top: el.style.top }));
      await page.evaluate(() => render(STATE));
      assert(await card.locator('.menu').evaluate(el => el.classList.contains('open')), 'Polling dismissed the menu');
      const menuAfter = await card.locator('.dd').evaluate(el => ({ width: el.clientWidth, height: el.clientHeight, scrollHeight: el.scrollHeight, left: el.style.left, top: el.style.top }));
      assert.equal(await card.locator('.dd').evaluate(el => el.scrollTop), scrollTop, `Polling reset menu scrolling at ${viewport.width}x${viewport.height}: ${JSON.stringify({ menuBefore, menuAfter })}`);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.menu.open').count(), 0, 'Escape did not close menu');
    }
    console.log('PASS: dropdown items remain reachable on desktop, mobile and short screens; polling and Escape work');
    if (process.argv.includes('--menu-only')) return;

    await page.setViewportSize({ width: 1100, height: 800 });
    const alice = page.locator('[data-account="alice"]');
    const dave = page.locator('[data-account="dave"]');
    const bob = page.locator('[data-account="bob"]');
    assert(await dave.locator('[data-act="resetcredit"]').isDisabled(), 'An account with no reset cards cannot use one');
    assert(await bob.locator('[data-act="resetcredit"]').isDisabled(), 'Expired credentials must be renewed before using a reset card');
    const resetRequests = [];
    let remainingCards = 1;
    let releaseReset;
    const resetResponseGate = new Promise(resolve => { releaseReset = resolve; });
    await page.route('**/api/state', async route => {
      const response = await route.fetch();
      const state = await response.json();
      state.accounts.find(a => a.name === 'alice').reset_credits = remainingCards;
      await route.fulfill({ json: state });
    });
    await page.route('**/api/reset/use', async route => {
      const body = route.request().postDataJSON();
      assert.equal(body.name, 'alice');
      assert.equal(body.confirmed, true);
      assert.match(body.request_id, /^[0-9a-f-]{36}$/);
      resetRequests.push(body);
      await resetResponseGate;
      if (resetRequests.length === 1) {
        remainingCards = 0;
        await route.fulfill({ status: 400, json: { error: 'response lost; outcome unknown' } });
      } else {
        remainingCards = 0;
        await route.fulfill({ json: { ok: true, code: 'reset', windows_reset: 2 } });
      }
    });
    await page.evaluate(() => tick());
    acceptDialogs = false;
    await alice.locator('[data-act="resetcredit"]').click();
    assert.equal(resetRequests.length, 0, 'Cancel must not use a reset card');
    acceptDialogs = true;
    await alice.locator('[data-act="resetcredit"]').click();
    await page.waitForFunction(() => resetCreditPending.has('acct-alice'));
    assert(await alice.locator('[data-act="resetcredit"]').isDisabled(), 'Duplicate reset must be disabled while pending');
    await page.evaluate(() => { void useResetCard('alice'); });
    const resetDialog = page.waitForEvent('dialog', { predicate: dialog => dialog.type() === 'alert' });
    releaseReset();
    await resetDialog;
    await page.waitForFunction(() => !resetCreditPending.has('acct-alice'));
    await page.reload();
    await alice.locator('[data-act="resetcredit"]').waitFor();
    assert(await alice.locator('[data-act="resetcredit"]').isEnabled(), 'An uncertain last-card reset must remain checkable after reload');
    await alice.locator('[data-act="resetcredit"]').click();
    await page.waitForFunction(() => document.getElementById('toastMsg').textContent === I18N.en.resetUsed);
    assert.equal(resetRequests.length, 2);
    assert.equal(resetRequests[0].request_id, resetRequests[1].request_id, 'Retry after reload must use the same operation id');
    assert.equal((await alice.locator('.reset-count b').textContent()).split(' ')[0], '0');
    assert(await alice.locator('[data-act="resetcredit"]').isDisabled(), 'Resolved reset with no cards left must be disabled');
    console.log('PASS: reset-card confirmation, disabled states, duplicate protection, timeout/reload retry, and refreshed counts');

    assert.equal(await bob.locator('.auth-recovery [data-act="refreshtoken"]').count(), 0);
    await bob.locator('.auth-recovery [data-act="relogin"]').scrollIntoViewIfNeeded();
    assert(await reachable(bob.locator('.auth-recovery [data-act="relogin"]')), 'Account without refresh token needs re-login');

    let refreshCount = 0;
    let succeed = false;
    let releaseResponse;
    const responseGate = new Promise(resolve => { releaseResponse = resolve; });
    await page.route('**/api/token/refresh', async route => {
      assert.equal(route.request().postDataJSON().name, 'dave');
      refreshCount++;
      await responseGate;
      await route.fulfill({ status: succeed ? 200 : 400, json: succeed ? { ok: true } : { error: 'refresh_token_expired <img src=x onerror=alert(1)>' } });
    });
    await page.route('**/api/login/cancel', route => route.fulfill({ json: { ok: true } }));
    await dave.locator('.auth-recovery [data-act="refreshtoken"]').click();
    await page.waitForFunction(() => tokenRefreshPending.has('dave'));
    assert(await dave.locator('.auth-recovery [data-act="refreshtoken"]').isDisabled(), 'Duplicate refresh must be disabled while renewing');
    await page.evaluate(() => { void refreshAccountToken('dave'); });
    releaseResponse();
    await page.locator('#tokenRefreshError').waitFor();
    assert.equal(await page.locator('#tokenRefreshError img').count(), 0, 'Refresh error must remain plain text');
    assert.match(await page.locator('#tokenRefreshError').textContent(), /refresh_token_expired/);
    await page.locator('#mOk').click();
    await page.locator('#tabBrowser').waitFor();
    assert.equal(await page.locator('#mTitle').textContent(), "Re-login 'dave'");
    await page.locator('#tabImport').click();
    assert(await page.locator('#mFile').isVisible(), 'Re-login must allow replacing auth.json');
    await page.locator('#mCancel').click();
    succeed = true;
    await dave.locator('.auth-recovery [data-act="refreshtoken"]').click();
    await page.waitForFunction(() => document.getElementById('toastMsg').textContent === I18N.en.tokenRefreshed);
    assert.equal(refreshCount, 2);
    console.log('PASS: expired accounts expose recovery; failed refresh offers re-login; successful refresh confirms completion');

    for (const lang of ['en', 'zh']) {
      await page.selectOption('#lang', lang);
      const resetLabel = await page.evaluate(() => t('useResetCard'));
      assert.equal(await alice.locator('[data-act="resetcredit"]').textContent(), resetLabel);
      await page.locator('#btnAdd').click();
      await page.locator('#tabImport').click();
      const encode = obj => Buffer.from(JSON.stringify(obj)).toString('base64url');
      const auth = JSON.stringify({ tokens: { access_token: `x.${encode({ exp: 1 })}.sig` } });
      await page.locator('#mFile').setInputFiles({ name: 'auth.json', mimeType: 'application/json', buffer: Buffer.from(auth) });
      await page.waitForFunction(() => document.getElementById('mFileInfo').textContent.includes(t('importExpiredHint')));
      await page.locator('#mCancel').click();
    }
    await page.selectOption('#lang','en');
    await page.evaluate(() => openLogin('bob',true));
    await page.locator('#tabImport').click();
    await page.locator('#mDrop').evaluate(el => {
      const data = new DataTransfer();
      data.items.add(new File([JSON.stringify({tokens:{access_token:'fixture'}})], 'auth.json', {type:'application/json'}));
      el.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:data}));
    });
    await page.waitForFunction(() => document.getElementById('mFileInfo')?.textContent.includes('Read'));
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#mTitle').textContent(), "Re-login 'bob'", 'Dropping a file in re-login must preserve the selected account');
    await page.locator('#mCancel').click();
    // Visual preferences, useful filters, and keyboard-only modal navigation.
    await page.selectOption('#theme','dark');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor),'rgb(17, 24, 32)');
    await page.reload();
    assert.equal(await page.locator('#theme').inputValue(),'dark');
    await page.selectOption('#theme','light');
    await page.locator('#overview [data-filter="attention"]').click();
    assert.equal(await page.locator('.card[data-account]').count(),2);
    await page.reload();
    assert.equal(await page.locator('#filters [aria-pressed="true"]').getAttribute('data-filter'),'attention');
    await page.locator('#filters [data-filter="all"]').click();
    await page.selectOption('#sort','name');
    const names=await page.locator('.card .title').allTextContents();
    assert.deepEqual(names,[...names].sort((a,b)=>a.localeCompare(b)));
    await page.locator('#search').fill('no-such-account');
    await page.locator('[data-act="clearfilters"]').click();
    assert.equal(await page.locator('.card[data-account]').count(),4);
    await page.locator('#btnAdd').click();
    await page.locator('#mName').waitFor();
    await page.locator('#mName').focus();
    await page.keyboard.press('Shift+Tab');
    assert(await page.locator('#mOk').evaluate(el=>el===document.activeElement),JSON.stringify(await page.evaluate(()=>({active:document.activeElement.id,items:[...modal.querySelectorAll('button,input,select,textarea,a[href]')].filter(el=>!el.disabled&&el.getClientRects().length).map(el=>el.id)}))));
    await page.keyboard.press('Tab');
    assert(await page.locator('#mName').evaluate(el=>el===document.activeElement));
    await page.keyboard.press('Escape');
    assert(await page.locator('#btnAdd').evaluate(el=>el===document.activeElement),'Modal must restore focus');
    console.log('PASS: saved theme/filter preferences, sorting, empty-state recovery, modal focus trap and return focus');

    // Concurrent triggers share a poll; a response remains usable after an interval elapses.
    await page.evaluate(async()=>{
      await tick();
      const original=api, snapshot=STATE;let release,count=0;
      api=async()=>{count++;return await new Promise(done=>release=done)};
      try {
        const first=tick(),joined=tick();
        if(first!==joined||count!==1)throw new Error('Concurrent polling was not coalesced');
        release({...snapshot,accounts:snapshot.accounts.map((a,i)=>i?a:{...a,display_name:'completed fixture'})});
        await Promise.all([first,joined]);
        if(STATE.accounts[0].display_name!=='completed fixture')throw new Error('A completed poll was discarded');
        api=async()=>({...snapshot,accounts:snapshot.accounts.map((a,i)=>i?a:{...a,display_name:'newest fixture'})});
        await tick();
        if(STATE.accounts[0].display_name!=='newest fixture')throw new Error('The next poll did not update state');
      } finally {api=original;render(snapshot)}
    });
    await page.route('**/api/state',route=>route.abort('failed'));
    await page.evaluate(()=>tick());
    assert(await page.locator('#connectionBanner').isVisible());
    assert.equal(await page.locator('.card[data-account]').count(),4,'Offline mode must retain the last good cards');
    await page.unroute('**/api/state');
    await page.locator('#retryConnection').click();
    await page.waitForFunction(()=>document.getElementById('connectionBanner').hidden);
    console.log('PASS: delayed polls cannot overwrite newer state; offline banner keeps cached cards and retry recovers');

    // Uniform latency longer than the two-second interval must not starve initial rendering.
    const slowPage=await browser.newPage();
    slowPage.on('pageerror',error=>errors.push(error.message));
    let slowRequests=0,slowActive=0,slowMaxActive=0;
    await slowPage.route('**/api/state',async route=>{
      slowRequests++;slowMaxActive=Math.max(slowMaxActive,++slowActive);
      const response=await route.fetch();
      await new Promise(done=>setTimeout(done,2500));
      try {await route.fulfill({response});}finally{slowActive--;}
    });
    await slowPage.goto(`http://127.0.0.1:${port}/?token=ui-test&lang=en`);
    await slowPage.locator('[data-account="alice"]').waitFor({timeout:8000});
    assert.equal(await slowPage.locator('.card[data-account]').count(),4);
    await slowPage.waitForFunction(()=>document.getElementById('accounts').getAttribute('aria-busy')==='false');
    await slowPage.evaluate(()=>tick());
    assert(slowRequests>=2,'Polling must continue after the initial slow response');
    assert.equal(slowMaxActive,1,'Slow polling must not accumulate concurrent requests');
    await slowPage.unrouteAll({behavior:'wait'});
    await slowPage.close();
    console.log('PASS: sustained slow responses render accounts, continue polling and keep one request in flight');

    // Browsers that deny local/session storage still render and allow preferences.
    const privatePage=await browser.newPage();
    privatePage.on('pageerror',error=>errors.push(error.message));
    await privatePage.addInitScript(()=>{Storage.prototype.getItem=()=>{throw new DOMException('Denied','SecurityError')};Storage.prototype.setItem=()=>{throw new DOMException('Denied','SecurityError')};Storage.prototype.removeItem=()=>{throw new DOMException('Denied','SecurityError')}});
    await privatePage.goto(`http://127.0.0.1:${port}/?token=ui-test&lang=en`);
    await privatePage.locator('[data-account="alice"]').waitFor();
    await privatePage.selectOption('#theme','dark');
    assert.equal(await privatePage.evaluate(()=>document.documentElement.dataset.theme),'dark');
    await privatePage.close();
    await page.selectOption('#theme','auto');
    await page.selectOption('#sort','ready');
    assert.deepEqual(errors, []);
    console.log('PASS: imported expired credentials show an honest expiry warning in English and Chinese; no JavaScript errors');
    if (process.env.CODEX_MONITOR_UI_SCREENSHOT) await page.screenshot({ path: process.env.CODEX_MONITOR_UI_SCREENSHOT, fullPage: true });
  } finally {
    if (browser) await browser.close();
    server.kill();
    await once(server, 'exit');
    rmSync(sandbox, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

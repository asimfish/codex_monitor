// Click the dashboard's remaining controls against real HTTP routes and disposable storage.
// Provider calls, browser launches and OS autostart changes are isolated in buttons_fixture.py.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/test_web_buttons.cjs
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { mkdtempSync, readFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const sandbox = mkdtempSync(path.join(tmpdir(), 'codex-buttons-'));
  const server = spawn(process.env.PYTHON || 'python3', ['-u', 'tests/buttons_fixture.py'], {
    cwd: path.resolve(__dirname, '..'),
    env: { ...process.env, BUTTONS_SANDBOX: sandbox },
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
    browser = await chromium.launch({ headless: true, args: ['--host-resolver-rules=MAP dashboard.fixture.test 127.0.0.1'] });
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    context.setDefaultTimeout(10000);
    const page = await context.newPage();
    const errors = [], requests = [], dialogs = [];
    let accept = true;
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async dialog => {
      dialogs.push({ type: dialog.type(), message: dialog.message() });
      if (accept) await dialog.accept();
      else await dialog.dismiss();
    });
    page.on('request', request => {
      if (request.method() === 'POST') requests.push({ path: new URL(request.url()).pathname, body: request.postDataJSON() });
    });
    const url = `http://127.0.0.1:${port}/?token=buttons-fixture&lang=en`;
    const card = name => page.locator(`[data-account="${name}"]`);
    const state = () => page.evaluate(() => STATE);
    const settle = () => page.waitForFunction(() => !actionPending.size && !statePoll);
    async function menu(name, action) {
      const account = card(name);
      if (!await account.locator('.menu').evaluate(el => el.classList.contains('open'))) await account.locator('[data-act="menu"]').click();
      await account.locator(`.dd [data-act="${action}"]`).click();
    }
    const toast = message => page.waitForFunction(message => document.getElementById('toastMsg').textContent.includes(message), message);
    const postCount = endpoint => requests.filter(r => r.path === endpoint).length;
    await page.goto(url);
    await card('alice').waitFor();

    if (process.argv.includes('--modal-only')) {
      let releaseToken, releaseSettings;
      const tokenGate = new Promise(resolve => { releaseToken = resolve; });
      const settingsGate = new Promise(resolve => { releaseSettings = resolve; });
      await page.route('**/api/token/refresh', async route => {
        await tokenGate;
        await route.fulfill({ status: 400, json: { error: 'fixture expired refresh token' } });
      });
      await page.route('**/api/autostart', async route => {
        await settingsGate;
        await route.fulfill({ json: { status: 'not installed' } });
      });
      try {
        const tokenRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/token/refresh');
        await menu('alice', 'refreshtoken');
        await tokenRequest;
        const settingsRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/autostart');
        await page.locator('#btnSettings').click();
        await settingsRequest;
        assert(await page.locator('#mOk').isDisabled(), 'Settings must stay disabled while loading');
        releaseToken();
        await page.locator('#tokenRefreshError').waitFor();
        assert(await page.locator('#mOk').isEnabled(), 'The re-login button must not inherit the replaced settings dialog disabled state');
        const settingsResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/autostart');
        releaseSettings();
        await settingsResponse;
        await page.locator('#mOk').click();
        await page.locator('#tabImport').waitFor();
        assert.equal(await page.locator('#mTitle').textContent(), "Re-login 'alice'");
        assert.deepEqual(errors, []);
        console.log('PASS: a token failure replaces loading settings with a usable re-login button; the stale settings response cannot overwrite it');
      } finally {
        releaseToken(); releaseSettings();
      }
      await page.locator('#mCancel').click();
      await page.unroute('**/api/autostart');

      let releaseSave, releaseNewSettings;
      const saveGate = new Promise(resolve => { releaseSave = resolve; });
      const newSettingsGate = new Promise(resolve => { releaseNewSettings = resolve; });
      await page.route('**/api/save', async route => { await saveGate; await route.continue(); });
      await page.route('**/api/autostart', async route => {
        await newSettingsGate;
        await route.fulfill({ json: { status: 'not installed' } });
      });
      try {
        await page.locator('.card [data-act="save"]').click();
        await page.locator('#mName').fill('saved-after-cancel');
        // Observe the old click handler settling, independently of the new dialog state.
        await page.evaluate(() => {
          const button = document.getElementById('mOk'), submit = button.onclick;
          window.oldModalSubmitSettled = false;
          button.onclick = async () => { try { await submit(); } finally { window.oldModalSubmitSettled = true; } };
        });
        const saveRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/save');
        await page.locator('#mOk').click();
        await saveRequest;
        await page.locator('#mCancel').click();
        const settingsRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/autostart');
        await page.locator('#btnSettings').click();
        await settingsRequest;
        assert(await page.locator('#mOk').isDisabled());
        releaseSave();
        await page.waitForFunction(() => window.oldModalSubmitSettled);
        assert(await page.locator('#modal').evaluate(el => el.classList.contains('open')), 'A completed save from a dismissed dialog must not close the new settings dialog');
        assert.equal(await page.locator('#mTitle').textContent(), 'Settings');
        assert(await page.locator('#mOk').isDisabled(), 'The old submission must not enable the new settings dialog while it is loading');
        releaseNewSettings();
        await page.locator('#mInterval').waitFor();
        assert(await page.locator('#mOk').isEnabled());
        assert.equal(JSON.parse(readFileSync(path.join(sandbox, 'accounts', 'saved-after-cancel', 'auth.json'))).tokens.account_id, 'acct-current');
        assert.deepEqual(errors, []);
        console.log('PASS: a dismissed save may complete in storage but cannot close or enable the newer settings dialog');
      } finally {
        releaseSave(); releaseNewSettings();
      }
      await page.locator('#mCancel').click();
      await page.unroute('**/api/autostart');

      let releaseCancel;
      const cancelGate = new Promise(resolve => { releaseCancel = resolve; });
      await page.route('**/api/login/cancel', async route => {
        await cancelGate;
        await route.fulfill({ json: {} });
      });
      try {
        await page.evaluate(() => {
          const button = document.getElementById('btnAdd'), open = button.onclick;
          window.oldLoginOpenSettled = false;
          button.onclick = async () => { try { await open(); } finally { window.oldLoginOpenSettled = true; } };
        });
        const cancelRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/login/cancel');
        await page.locator('#btnAdd').click();
        await cancelRequest;
        await page.keyboard.press('Escape');
        await page.locator('#btnSettings').click();
        await page.locator('#mInterval').waitFor();
        releaseCancel();
        await page.waitForFunction(() => window.oldLoginOpenSettled);
        assert.equal(await page.locator('#mTitle').textContent(), 'Settings', 'An abandoned login preparation must not reopen over the settings dialog');
        assert(await page.locator('#mInterval').isVisible());
        assert(await page.locator('#mOk').isEnabled());
        assert.deepEqual(errors, []);
        console.log('PASS: abandoning login preparation prevents its late response from reopening a dialog');
      } finally {
        releaseCancel();
      }
      return;
    }

    // A rejected clipboard write must never report success or leak an unhandled rejection.
    if (process.argv.includes('--clipboard-only')) {
      await page.locator('#btnAdd').click();
      await page.locator('#mName').fill('login-fixture');
      await page.locator('#mOk').click();
      await page.locator('#copyUrl').waitFor();
      await page.evaluate(() => {
        window.clipboardWrite = navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText = () => Promise.reject(new DOMException('Fixture permission denied', 'NotAllowedError'));
        document.getElementById('toastMsg').textContent = '';
      });
      const copyOutcome = Promise.race([page.waitForEvent('dialog'), page.waitForEvent('pageerror')]);
      await page.locator('#copyUrl').click();
      await copyOutcome;
      assert.notEqual(await page.locator('#toastMsg').textContent(), 'copied', 'Denied copy must not announce success');
      assert(dialogs.some(d => /copy|clipboard/i.test(d.message)), 'Copy failure must be visible');
      assert.deepEqual(errors, [], 'Copy failure must not become an unhandled rejection');
      await page.evaluate(() => { navigator.clipboard.writeText = window.clipboardWrite; });
      await page.locator('#copyUrl').click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://fixture.invalid/authorize');
      await page.evaluate(() => {
        Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
        document.execCommand = () => false;
        document.getElementById('toastMsg').textContent = '';
      });
      const before = dialogs.length;
      await page.locator('#copyUrl').click();
      assert.equal(dialogs.length, before + 1, 'An unavailable legacy clipboard must also show an error');
      assert.equal(await page.locator('#toastMsg').textContent(), '');
      assert.equal(await page.locator('body > textarea').count(), 0, 'Clipboard fallback must remove credential-bearing temporary fields');
      console.log('PASS: denied login clipboard write reports failure without a false success');
      return;
    }

    if (process.argv.includes('--http-only')) {
      const remote = await browser.newPage();
      remote.setDefaultTimeout(10000);
      const remoteErrors = [], remoteDialogs = [], resetRequests = [];
      remote.on('pageerror', error => remoteErrors.push(error.message));
      remote.on('dialog', async dialog => { remoteDialogs.push(dialog.message()); await dialog.accept(); });
      remote.on('request', request => { if (new URL(request.url()).pathname === '/api/reset/use') resetRequests.push(request.postDataJSON()); });
      await remote.goto(`http://dashboard.fixture.test:${port}/?token=buttons-fixture&lang=en`);
      await remote.locator('[data-account="alice"]').waitFor();
      assert.equal(await remote.evaluate(() => isSecureContext), false, 'This case must exercise an HTTP domain, not trusted loopback');
      if (process.argv.includes('--reset-only')) {
        await remote.locator('[data-account="alice"] [data-act="resetcredit"]').click();
        await remote.waitForFunction(() => !resetCreditPending.size);
        assert.equal(resetRequests.length, 1, `HTTP-domain reset never reached the API: ${JSON.stringify(remoteDialogs)}`);
        assert.match(resetRequests[0].request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        assert.equal(resetRequests[0].confirmed, true);
        assert.equal((await remote.locator('[data-account="alice"] .reset-count b').textContent()).split(' ')[0], '1');
        console.log('PASS: reset uses a cryptographically generated operation ID on HTTP domains');
      } else {
        await remote.locator('[data-account="alice"] [data-act="menu"]').click();
        for (const action of ['copypath', 'copy', 'copyrun', 'copyenv']) {
          await remote.locator('[data-account="alice"] [data-act="' + action + '"]').click();
          await remote.waitForFunction(() => !actionPending.size);
          assert.equal(await remote.locator('#toastMsg').textContent(), 'copied', `HTTP-domain copy failed: ${JSON.stringify(remoteDialogs)}`);
          const actual = await page.evaluate(() => navigator.clipboard.readText());
          if (action === 'copypath') assert.equal(actual, (await state()).accounts.find(a => a.name === 'alice').auth_path);
          if (action === 'copy') assert.equal(JSON.parse(actual).tokens.account_id, 'acct-alice');
          if (action === 'copyrun') assert.equal(actual, "codex-monitor run 'alice'");
          if (action === 'copyenv') assert.equal(actual, 'eval "$(codex-monitor env \'alice\')"');
        }
        assert.deepEqual(remoteDialogs, [], 'Available legacy clipboard copy must not show an error');
        console.log('PASS: clipboard buttons work on HTTP domains without the secure Clipboard API');
      }
      assert.deepEqual(remoteErrors, []);
      await remote.close();
      return;
    }

    if (!process.argv.includes('--undo-only')) {
      assert(await card('guest').locator('[data-act="copy"]').isDisabled());
      assert(await card('guest').locator('[data-act="download"]').isDisabled());
      await card('alice').locator('[data-act="menu"]').click();
      await page.keyboard.press('ArrowDown');
      assert(await card('alice').locator('[data-act="rename"]').evaluate(el => el === document.activeElement));
      await page.keyboard.press('End');
      assert(await card('alice').locator('[data-act="refreshtoken"]').evaluate(el => el === document.activeElement));
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.menu.open').count(), 0);
      await page.locator('.card [data-act="save"]').click();
      await page.locator('#mName').fill('cancelled-save');
      await page.locator('#mCancel').click();
      assert.equal(postCount('/api/save'), 0);
      await page.locator('.card [data-act="save"]').click();
      await page.locator('#mName').fill('saved-current');
      await page.locator('#mForce').check();
      await page.locator('#mOk').click();
      await page.waitForFunction(() => STATE.accounts.some(a => a.name === 'saved-current'));
      assert.equal(JSON.parse(readFileSync(path.join(sandbox, 'accounts', 'saved-current', 'auth.json'))).tokens.account_id, 'acct-current');
      assert.equal(requests.find(r => r.path === '/api/save').body.force, true);
      console.log('PASS: main-only Save as supports cancel, overwrite selection and persisted account creation');
    }

    // Observe real credentials changing, then exercise failed and successful Undo.
    accept = false;
    await card('alice').locator('[data-act="switch"]').click();
    await settle();
    assert.equal(postCount('/api/switch'), 0, 'Cancelled switch must not reach the API');
    accept = true;
    await card('alice').locator('[data-act="switch"]').click();
    await page.waitForFunction(() => STATE.accounts.find(a => a.name === 'alice')?.active);
    assert.equal(JSON.parse(readFileSync(path.join(sandbox, 'codex', 'auth.json'))).tokens.account_id, 'acct-alice');
    await page.locator('#toastAct').click();
    await page.waitForFunction(() => !STATE.accounts.find(a => a.name === 'alice')?.active);
    await card('bob').locator('[data-act="switch"]').click();
    await page.waitForFunction(() => STATE.accounts.find(a => a.name === 'bob')?.active);
    const count = postCount('/api/switch');
    let releaseUndo;
    const undoGate = new Promise(resolve => { releaseUndo = resolve; });
    await page.route('**/api/switch', async route => {
      await undoGate;
      await route.fulfill({ status: 400, json: { error: 'fixture undo rejected' } });
    });
    const undoRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/switch');
    await page.locator('#toastAct').click();
    await undoRequest;
    assert(await page.locator('#toastAct').isDisabled(), 'Undo must prevent duplicate pending switches');
    releaseUndo();
    await toast('fixture undo rejected');
    assert.equal(postCount('/api/switch'), count + 1);
    assert(await page.locator('#toastAct').isEnabled(), 'Failed Undo must remain retryable');
    assert.deepEqual(errors, [], 'Failed Undo must not leak an unhandled rejection');
    await page.unroute('**/api/switch');
    await page.locator('#toastAct').click();
    await page.waitForFunction(() => !STATE.accounts.find(a => a.name === 'bob')?.active);
    console.log('PASS: switch cancellation, persisted activation, Undo failure feedback, duplicate protection and retry');
    if (process.argv.includes('--undo-only')) return;

    const previousRefresh = (await state()).accounts.find(a => a.name === 'alice').source_at;
    await menu('alice', 'refreshone');
    await settle();
    await page.waitForFunction(previous => STATE.accounts.find(a => a.name === 'alice').source_at !== previous, previousRefresh);
    const beforeBulkRefresh = (await state()).accounts.find(a => a.name === 'alice').source_at;
    await page.locator('#btnRefresh').click();
    await page.waitForFunction(previous => !STATE.refreshing && STATE.accounts.find(a => a.name === 'alice').source_at !== previous, beforeBulkRefresh);
    assert(requests.some(r => r.path === '/api/refresh' && r.body === null), 'Global refresh must use the bulk API');
    console.log('PASS: account and global quota refresh trigger the real API and publish updated state');

    for (const action of ['copy', 'copypath', 'copyrun', 'copyenv']) {
      await menu('alice', action);
      await settle();
      const actual = await page.evaluate(() => navigator.clipboard.readText());
      const account = (await state()).accounts.find(a => a.name === 'alice');
      if (action === 'copy') assert.equal(JSON.parse(actual).tokens.account_id, 'acct-alice');
      if (action === 'copypath') assert.equal(actual, account.auth_path);
      if (action === 'copyrun') assert.equal(actual, "codex-monitor run 'alice'");
      if (action === 'copyenv') assert.equal(actual, 'eval "$(codex-monitor env \'alice\')"');
    }
    const downloadPromise = page.waitForEvent('download');
    await menu('alice', 'download');
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), 'auth-alice.json');
    assert.equal(JSON.parse(readFileSync(await download.path())).tokens.account_id, 'acct-alice');
    console.log('PASS: all four account copy actions preserve exact content and auth download returns the selected account');

    await page.locator('#btnLog').click();
    assert(await page.locator('#log').isVisible());
    assert.match(await page.locator('#log').textContent(), /switched|activated|archived/i);
    await page.locator('#btnLog').click();
    assert(!await page.locator('#log').isVisible());
    await page.locator('#btnToggleAll').click();
    assert.equal(await page.locator('.card.compact').count(), (await state()).accounts.length);
    await page.reload();
    await card('alice').waitFor();
    assert.equal(await page.locator('.card.compact').count(), (await state()).accounts.length);
    await page.locator('#btnToggleAll').click();
    assert.equal(await page.locator('.card.compact').count(), 0);
    for (const filter of ['all', 'ready', 'limited', 'attention', 'tagged']) {
      await page.locator(`#filters [data-filter="${filter}"]`).click();
      assert.equal(await page.locator('#filters [aria-pressed="true"]').getAttribute('data-filter'), filter);
      const expected = (await state()).accounts.filter(a => filter === 'all' || (filter === 'ready' && a.availability === 'ready') || (filter === 'limited' && a.availability === 'limited') || (filter === 'attention' && ['expired','error','signed_out','manual'].includes(a.availability)) || (filter === 'tagged' && a.tags.length)).map(a => a.name).sort();
      assert.deepEqual((await page.locator('.card[data-account]').evaluateAll(cards => cards.map(card => card.dataset.account))).sort(), expected);
    }
    await page.locator('#overview [data-filter="all"]').click();
    await page.locator('#search').fill('alice');
    assert.equal(await page.locator('.card[data-account]').count(), 1);
    await page.locator('#clearSearch').click();
    assert.equal(await page.locator('#search').inputValue(), '');
    for (const sort of ['quota', 'reset', 'name', 'ready']) {
      await page.selectOption('#sort', sort);
      const snapshot = await state();
      const visible = await page.locator('.card[data-account]').evaluateAll(cards => cards.map(card => card.dataset.account));
      const ordered = visible.map(name => snapshot.accounts.find(a => a.name === name));
      if (sort === 'name') assert.deepEqual(ordered.map(a => a.display_name), ordered.map(a => a.display_name).sort((a,b) => a.localeCompare(b)));
      if (sort === 'quota') assert(ordered.every((a,i) => !i || (ordered[i-1].tightest_remaining ?? -1) >= (a.tightest_remaining ?? -1)));
      if (sort === 'reset') {
        const times = ordered.map(a => Math.min(...a.windows.map(w => w.reset_at ? Date.parse(w.reset_at) : Infinity)));
        assert(times.every((time,i) => !i || times[i-1] <= time));
      }
      if (sort === 'ready') assert.deepEqual(visible, snapshot.accounts.map(a => a.name));
    }
    console.log('PASS: log visibility, persistent expand/collapse, every filter, search clearing and sorting selectors');

    await page.locator('#btnSettings').click();
    await page.locator('#mInterval').waitFor();
    const settingsCount = postCount('/api/settings');
    await page.locator('#mInterval').fill('9');
    await page.locator('#mOk').click();
    assert.equal(postCount('/api/settings'), settingsCount, 'Invalid settings must not be submitted');
    assert(await page.locator('#modal').evaluate(el => el.classList.contains('open')));
    await page.locator('#mInterval').fill('47');
    await page.locator('#mSkipSwitch').check();
    await page.locator('#autoOn').click();
    await toast('fixture autostart installed');
    await page.locator('#autoOff').click();
    await toast('fixture autostart removed');
    await page.locator('#btnSync').click();
    await toast('Nothing to sync');
    await page.locator('#mOk').click();
    await page.waitForFunction(() => !modal.classList.contains('open') && STATE.interval === 47);
    await page.locator('#btnSettings').click();
    await page.locator('#mInterval').waitFor();
    assert.equal(await page.locator('#mInterval').inputValue(), '47');
    assert(await page.locator('#mSkipSwitch').isChecked());
    await page.locator('#mInterval').fill('10');
    await page.locator('#mCancel').click();
    assert.equal((await state()).interval, 47, 'Cancel must discard an edited interval');
    console.log('PASS: settings validation/save/cancel, sync, and autostart enable/disable dispatch through real HTTP routes');

    await menu('alice', 'tags');
    await page.locator('#tagInput').fill('cancelled tag');
    await page.locator('#tagAddBtn').click();
    await page.locator('#tagCancel').click();
    assert(!(await state()).accounts.find(a => a.name === 'alice').tags.includes('cancelled tag'));

    await page.locator('#btnAdd').click();
    await page.locator('#mOk').click();
    assert(await page.locator('#mNameErr').isVisible());
    await page.locator('#tabImport').click();
    await page.locator('#mPasteToggle').click();
    await page.locator('#mName').fill('imported');
    const claims = { exp: Math.floor(Date.now() / 1000) + 86400, 'https://api.openai.com/auth': { chatgpt_account_id: 'acct-imported', chatgpt_plan_type: 'plus' } };
    const importedAuth = { tokens: { access_token: `fixture.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.demo`, account_id: 'acct-imported' } };
    await page.locator('#mPaste').fill(JSON.stringify(importedAuth));
    await page.locator('#mOk').click();
    await page.waitForFunction(() => document.getElementById('mOk').textContent === I18N.en.makeActive);
    await card('imported').waitFor();
    await page.locator('#mOk').click();
    await page.waitForFunction(() => !modal.classList.contains('open') && STATE.accounts.find(a => a.name === 'imported')?.active);
    assert.equal(JSON.parse(readFileSync(path.join(sandbox, 'codex', 'auth.json'))).tokens.account_id, 'acct-imported');
    console.log('PASS: add-account name validation, paste toggle/import and Make active persist the selected credentials');

    await menu('alice', 'relogin');
    await page.locator('#tabImport').click();
    await page.locator('#tabBrowser').click();
    await page.locator('#mOk').click();
    await page.locator('#copyUrl').waitFor();
    await page.locator('#copyUrl').click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://fixture.invalid/authorize');
    await page.locator('#openPriv').click();
    await toast('FixtureBrowser');
    await page.locator('#openDef').click();
    await toast('default browser');
    await page.locator('#toDevice').click();
    await page.locator('#copyCode').click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'ABCD-EFGH');
    await page.locator('#openPriv').click();
    await toast('FixtureBrowser');
    await page.locator('#openDef').click();
    await toast('default browser');
    await page.locator('#toBrowser').click();
    await page.locator('#copyUrl').waitFor();
    await page.locator('#mCancel').click();
    await page.locator('#btnAdd').click();
    await page.locator('#mName').fill('login-failure');
    await page.locator('#mOk').click();
    await page.locator('#retryB').click();
    await page.locator('#retryD').click();
    await page.locator('#copyCode').waitFor();
    await page.keyboard.press('Escape');
    assert(!await page.locator('#modal').evaluate(el => el.classList.contains('open')));
    assert(requests.some(r => r.path === '/api/login/start' && r.body.relogin === true));
    assert(requests.some(r => r.path === '/api/login/start' && r.body.mode === 'device'));
    console.log('PASS: re-login tabs, login URL/code copy, both browser-open choices, mode switching, failure retries and cancel');
    const empty = await context.newPage();
    empty.on('pageerror', error => errors.push(error.message));
    await empty.route('**/api/state', async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      await route.fulfill({ json: { ...snapshot, accounts: [] } });
    });
    await empty.goto(url);
    await empty.locator('[data-act="add"]').click();
    await empty.locator('#mName').waitFor();
    await empty.locator('#mCancel').click();
    await empty.close();
    console.log('PASS: the empty-dashboard Add account button opens a usable dialog');
    assert.deepEqual(errors, [], 'All controls must finish without JavaScript errors');
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) { server.kill(); await once(server, 'exit'); }
    rmSync(sandbox, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

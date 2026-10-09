// Real-browser regression checks using Chrome's DevTools protocol. No npm dependencies.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const directory = await mkdtemp(join(tmpdir(), 'codex-web-browser-'));
const output = resolve(process.env.SCREENSHOT_DIR || join(directory, 'screenshots'));
await mkdir(output, {recursive: true});
const chromePath = process.env.CHROME_BIN || (process.platform === 'darwin'
  ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : 'google-chrome');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
let chrome, fixture, socket;
const errors = [];
const delay = ms => new Promise(done => setTimeout(done, ms));
const until = async (test, label) => {
  for (let i = 0; i < 100; i++) { const value = await test(); if (value) return value; await delay(100); }
  throw new Error('Timed out: ' + label);
};
const stop = async child => {
  if (!child || child.exitCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([new Promise(done => child.once('exit', done)), delay(3000)]);
  if (child.exitCode === null) child.kill('SIGKILL');
};

try {
  fixture = spawn(python, ['tests/dashboard_fixture.py'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  let fixtureText = '', fixtureErrors = '';
  fixture.stdout.on('data', bytes => { fixtureText += bytes; });
  fixture.stderr.on('data', bytes => { fixtureErrors += bytes; });
  const url = await until(() => {
    if (fixture.exitCode !== null) throw new Error('Fixture failed: ' + fixtureErrors);
    return fixtureText.match(/http:\/\/[^\s]+/)?.[0];
  }, 'fixture URL');
  chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--no-sandbox', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--user-data-dir=' + directory, 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
  let chromeErrors = '';
  chrome.stderr.on('data', bytes => { chromeErrors += bytes; });
  const port = await until(async () => {
    if (chrome.exitCode !== null) throw new Error('Chrome failed: ' + chromeErrors.slice(-1000));
    return readFile(join(directory, 'DevToolsActivePort'), 'utf8').then(value => value.split('\n')[0]).catch(() => null);
  }, 'Chrome debug port');
  const page = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((done, reject) => { socket.addEventListener('open', done, {once: true}); socket.addEventListener('error', reject, {once: true}); });
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      const {done, reject, timer} = pending.get(data.id); pending.delete(data.id); clearTimeout(timer);
      data.error ? reject(new Error(data.error.message)) : done(data.result);
    }
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.text);
    if (data.method === 'Log.entryAdded' && data.params.entry.level === 'error') errors.push(data.params.entry.text);
  });
  const call = (method, params = {}) => new Promise((done, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 15000);
    pending.set(id, {done, reject, timer}); socket.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  const screenshot = async (filename, clip) => {
    const {data} = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false, ...(clip ? {clip} : {})});
    await writeFile(join(output, filename), Buffer.from(data, 'base64'));
  };
  await call('Runtime.enable'); await call('Page.enable'); await call('Log.enable');
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false});
  await call('Page.navigate', {url});
  await until(() => evaluate("document.querySelectorAll('.card[data-account]').length===9"), 'account cards');
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.card')].slice(0,3).map(el=>el.dataset.account)"), ['erin', 'alice', 'bob']);
  const alice = "document.querySelector('[data-account=alice]')";
  const text = await evaluate(alice + '.innerText');
  for (const label of ['重置次数', '订阅到期', '凭证有效期', '余额', '12.50', 'Code review', 'GPT-5.3-Codex-Spark']) assert.ok(text.includes(label), 'Missing detail: ' + label);
  assert.match(await evaluate(alice + ".querySelector('.reset-at').innerText"), /\d{4}\/\d{2}\/\d{2}/);
  await screenshot('dashboard-zh.png');
  for (const [width, columns] of [[1440, 3], [1000, 2], [390, 1], [320, 1]]) {
    await call('Emulation.setDeviceMetricsOverride', {width, height: 1100, deviceScaleFactor: 1, mobile: false});
    const layout = await evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth,columns:getComputedStyle(document.getElementById('accounts')).gridTemplateColumns.split(' ').length})");
    assert.equal(layout.scroll, layout.width, 'Horizontal overflow at ' + width);
    assert.equal(layout.columns, columns, 'Columns at ' + width);
    if (width === 390) await screenshot('dashboard-mobile-zh.png');
  }
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false});
  await call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-color-scheme', value: 'dark'}]});
  await delay(200);
  assert.equal(await evaluate("getComputedStyle(document.body).backgroundColor"), 'rgb(17, 24, 32)');
  await screenshot('dashboard-dark-zh.png');
  await call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-color-scheme', value: 'light'}]});

  // A single-card collapse leaves all the other cards expanded.
  await evaluate(alice + ".querySelector('[data-act=collapse]').click()");
  assert.equal(await evaluate("document.querySelectorAll('.card.compact').length"), 1);
  await evaluate(alice + ".querySelector('[data-act=expand]').click()");
  // Dropdowns and error details stay open during the two-second polling cycle.
  await evaluate(alice + ".querySelector('[data-act=menu]').click()");
  await evaluate('tick()');
  assert.equal(await evaluate(alice + ".querySelector('.menu').classList.contains('open')"), true);
  await evaluate("document.querySelector('[data-account=dave] details').open=true; tick()");
  assert.equal(await evaluate("document.querySelector('[data-account=dave] details').open"), true);
  await evaluate("document.getElementById('title').click()");

  // Save a literal HTML-looking tag through the actual dialog, then reload.
  await evaluate(alice + ".querySelector('[data-act=tags]').click()");
  await evaluate("document.getElementById('tagInput').value='未删除但用不了';document.getElementById('tagInput').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));document.getElementById('tagUnavailable').checked=true");
  const dialogClip = await evaluate("(()=>{const r=document.getElementById('tagDialog').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1}})()");
  await screenshot('dashboard-tags-zh.png', dialogClip);
  await evaluate("document.getElementById('tagInput').value='<b>literal</b>';document.getElementById('tagAddBtn').click();document.getElementById('tagSave').click()");
  await until(() => evaluate("!document.getElementById('tagDialog').open"), 'tag save');
  await evaluate('tick()');
  assert.equal(await evaluate(alice + ".classList.contains('state-manual')"), true);
  assert.equal(await evaluate(alice + ".querySelectorAll('.tag b').length"), 0, 'Tags must render as text');
  assert.ok((await evaluate(alice + ".querySelector('.tags-row').innerText")).includes('<b>literal</b>'));
  await call('Page.reload');
  await until(() => evaluate("document.querySelector('[data-account=alice]')?.classList.contains('state-manual')"), 'persistent tag on reload');
  await evaluate("document.querySelector('[data-filter=tagged]').click();document.getElementById('search').value='未删除但用不了';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}))");
  assert.equal(await evaluate("document.querySelectorAll('.card[data-account]').length"), 1, 'Tag search');
  await evaluate(alice + ".querySelector('[data-act=tags]').click()");
  await evaluate("[...document.querySelectorAll('#tagList .editable')].find(el=>el.querySelector('span').textContent==='未删除但用不了').querySelector('button').click();document.getElementById('tagUnavailable').checked=false;document.getElementById('tagSave').click()");
  await until(() => evaluate("!document.getElementById('tagDialog').open"), 'tag removal');
  await evaluate("document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input'));document.querySelector('[data-filter=all]').click();tick()");
  assert.equal(await evaluate(alice + ".classList.contains('state-ready')"), true);
  assert.ok(!(await evaluate(alice + ".querySelector('.tags-row').innerText")).includes('未删除但用不了'));
  await evaluate("document.getElementById('lang').value='en';document.getElementById('lang').dispatchEvent(new Event('change'))");
  assert.equal(await evaluate("document.documentElement.lang"), 'en');
  assert.ok((await evaluate(alice + '.innerText')).includes('Credentials until'));
  await screenshot('dashboard-en.png');
  assert.deepEqual(errors, [], 'Browser errors');
  console.log('Browser checks passed: responsive layout, sorting, full details, light/dark, EN/ZH, persistent tags, filtering, escaping and interactions.');
  if (process.env.SCREENSHOT_DIR) console.log('Screenshots: ' + output);
} finally {
  socket?.close();
  await stop(chrome); await stop(fixture);
  await rm(directory, {recursive: true, force: true});
}

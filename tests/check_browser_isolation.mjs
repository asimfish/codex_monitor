// Verify the production private-launch flags with real Chromium cookie stores.
import assert from 'node:assert/strict';
import {execFileSync, spawn} from 'node:child_process';
import {readFile, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const chrome=process.env.CHROME_BIN||(process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':'google-chrome');
const python=process.env.PYTHON||(process.platform==='win32'?'python':'python3');
const commands=JSON.parse(execFileSync(python,['-c',`
import json
from unittest.mock import Mock, patch
from codex_monitor import browsers
process=Mock()
process.wait.return_value=0
with patch.object(browsers.paths,'IS_MAC',False), patch.object(browsers,'find_private_browser',return_value=('Google Chrome',[${JSON.stringify(chrome)},'--incognito'])), patch.object(browsers.subprocess,'Popen',return_value=process) as launch:
    browsers.open_private('about:blank')
    browsers.open_private('about:blank')
print(json.dumps([call.args[0] for call in launch.call_args_list]))
`],{cwd:root,encoding:'utf8'}));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const browsers=[];
const connect=async argv=>{
  const profile=argv.find(arg=>arg.startsWith('--user-data-dir='))?.split('=').slice(1).join('=');
  assert(profile,'Private launch must select its own profile');
  const child=spawn(argv[0],[...argv.slice(1,-1),'--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',argv.at(-1)],{detached:process.platform!=='win32',stdio:'ignore'});
  const browser={profile,child};browsers.push(browser);
  let port;
  for(let n=0;n<300;n++){
    assert.equal(child.exitCode,null,'Isolated browser exited too soon');
    try{port=(await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break}catch{}
    await delay(100);
  }
  assert(port,'Isolated browser did not start');
  const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page=pages.find(target=>target.type==='page');
  const socket=new WebSocket(page.webSocketDebuggerUrl);browser.socket=socket;
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
  let id=0;const pending=new Map();
  socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(pending.has(message.id)){const {resolve,reject,timer}=pending.get(message.id);pending.delete(message.id);clearTimeout(timer);message.error?reject(new Error(message.error.message)):resolve(message.result)}});
  browser.call=(method,params={})=>new Promise((resolve,reject)=>{const seq=++id;const timer=setTimeout(()=>{pending.delete(seq);reject(new Error('CDP timeout: '+method))},10000);pending.set(seq,{resolve,reject,timer});socket.send(JSON.stringify({id:seq,method,params}))});
  return browser;
};
try{
  assert.notEqual(commands[0].find(a=>a.startsWith('--user-data-dir=')),commands[1].find(a=>a.startsWith('--user-data-dir=')));
  const first=await connect(commands[0]);
  const url='http://127.0.0.1/';
  await first.call('Network.setCookie',{name:'login_fixture',value:'previous-account',url});
  assert.equal((await first.call('Network.getCookies',{urls:[url]})).cookies.length,1);
  const second=await connect(commands[1]);
  assert.equal((await second.call('Network.getCookies',{urls:[url]})).cookies.length,0,'New login must not inherit previous private-window cookies');
  assert.equal((await first.call('Network.getCookies',{urls:[url]})).cookies.length,1,'Existing browser session must remain intact');
  console.log('Private browser isolation passed: two production launch commands use separate cookie stores; existing session preserved.');
}finally{
  for(const browser of browsers){
    browser.socket?.close();
    if(browser.child.exitCode===null){
      if(process.platform==='win32')browser.child.kill();else try{process.kill(-browser.child.pid,'SIGTERM')}catch(error){if(error.code!=='ESRCH')throw error}
      await Promise.race([new Promise(resolve=>browser.child.once('exit',resolve)),delay(3000)]);
      if(process.platform!=='win32')try{process.kill(-browser.child.pid,'SIGKILL')}catch(error){if(error.code!=='ESRCH')throw error}
    }
    await rm(browser.profile,{recursive:true,force:true});
  }
}

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { chromium } = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'tmp', 'lifecycle-qa');
fs.mkdirSync(output, { recursive: true });
process.env.TEMP = path.join(root, 'tmp'); process.env.TMP = process.env.TEMP;
const base = 'http://localhost:8001'; // isolated acceptance database
const results = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(test, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await test()) return; await sleep(500); }
  throw new Error('Expected lifecycle state did not arrive before timeout');
}

(async () => {
  const session = await fetch(base + '/api/auth/login', { method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'child',password:'Laoyou123!'}) }).then(r=>r.json());
  const headers = {Authorization:'Bearer '+session.token,'Content-Type':'application/json'};
  async function request(endpoint, data, method) {
    const response = await fetch(base+'/api'+endpoint,{headers,method:method||(data?'POST':'GET'),...(data?{body:JSON.stringify(data)}:{})});
    assert.ok(response.ok, `${endpoint}: ${response.status}`);
    return response.json();
  }
  const [elder] = await request('/elders');
  const browser = await chromium.launch({executablePath:'D:/Chrome/Application/chrome.exe',headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'],env:{...process.env}});
  const context = await browser.newContext({viewport:{width:1536,height:1024},permissions:['camera','microphone']});
  await context.addInitScript(() => {
    window.__spoken = [];
    window.SpeechSynthesisUtterance = class { constructor(text) { this.text=text; } };
    Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{speaking:false,pending:false,
      speak(utterance){this.speaking=true;window.__spoken.push(utterance.text);setTimeout(()=>{this.speaking=false;utterance.onend?.();},30);},
      cancel(){this.speaking=false;},getVoices(){return[];}}});
  });
  const page = await context.newPage();
  const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
  let reminder;
  try {
    await request(`/elders/${elder.id}/settings`,{camera_enabled:true,voice_enabled:true},'PATCH');
    await page.goto(base);
    await page.getByLabel('账号',{exact:true}).fill('elder');
    await page.getByLabel('密码',{exact:true}).fill('Laoyou123!');
    await page.getByRole('button',{name:'进入老友',exact:true}).click();
    await page.locator('.side-nav').waitFor();
    const due = new Date(Date.now()+60000);
    const clock = new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(due);
    const title = '跨页面播报验收-' + Date.now();
    reminder=await request(`/elders/${elder.id}/reminders`,{title,medicine:'测试文字，不是真实用药',dose:'演示',time:clock});
    console.log('WAIT scheduled reminder at '+clock+' while checking camera lifecycle');

    await page.getByRole('button',{name:'安全设置',exact:true}).click();
    await page.getByRole('button',{name:'启动本机预览',exact:true}).click();
    await until(async()=>{const r=await fetch(base+`/api/elders/${elder.id}/snapshot`,{headers});return r.status===200;});
    await page.getByRole('button',{name:'首页',exact:true}).click();
    const first = await fetch(base+`/api/elders/${elder.id}/snapshot`,{headers}).then(r=>r.headers.get('x-captured-at'));
    await until(async()=>{const r=await fetch(base+`/api/elders/${elder.id}/snapshot`,{headers});return r.ok&&r.headers.get('x-captured-at')!==first;},40000);
    results.push('camera continues uploading after leaving the preview page');
    console.log('PASS camera continues across navigation');

    await request(`/elders/${elder.id}/settings`,{camera_enabled:false,confirm_camera_off:true},'PATCH');
    await page.getByRole('button',{name:'安全设置',exact:true}).click();
    await page.getByText('未开始预览',{exact:true}).waitFor({timeout:15000});
    assert.equal((await fetch(base+`/api/elders/${elder.id}/snapshot`,{headers})).status,409);
    results.push('child remote camera shutdown stops elder capture and blocks snapshots');
    console.log('PASS remote shutdown releases preview');

    await page.getByRole('button',{name:'用药提醒',exact:true}).click();
    await until(async()=>{
      const rows=await request(`/elders/${elder.id}/broadcasts`);
      return rows.some(item=>item.reminder_id===reminder.id&&item.played_at);
    },80000);
    const spoken=await page.evaluate(()=>window.__spoken);
    assert.equal(spoken.filter(text=>text.includes(title)).length,1);
    assert.ok(page.url().endsWith('/reminders'));
    results.push('scheduled reminder completes once on another page; synthesized completion is stubbed');
    console.log('PASS scheduled reminder across pages and exact acknowledgement');
    await page.screenshot({path:path.join(output,'reminder-lifecycle.png'),fullPage:true});
    assert.deepEqual(pageErrors,[]);
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,checks:results,note:'Synthetic media and a speech-synthesis completion stub; no claim of acoustic ASR accuracy.'},null,2));
  } catch(error) {
    await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:false,checks:results,failure:error.message,pageErrors},null,2));
    throw error;
  } finally {
    if(reminder) await request('/reminders/'+reminder.id,undefined,'DELETE');
    await request(`/elders/${elder.id}/settings`,{camera_enabled:elder.camera_enabled,voice_enabled:elder.voice_enabled,...(!elder.camera_enabled?{confirm_camera_off:true}:{})},'PATCH');
    await browser.close();
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});

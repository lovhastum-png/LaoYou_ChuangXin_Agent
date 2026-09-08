/* Browser acceptance using isolated profiles and synthetic camera/microphone.
 * TEMP/TMP and every artifact must point into the project workspace.
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { chromium } = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'tmp', 'web-qa');
fs.mkdirSync(output, { recursive: true });
process.env.TEMP = path.join(root, 'tmp');
process.env.TMP = process.env.TEMP;
const base = process.env.LAOYOU_WEB_BASE || 'http://localhost:8000';
const checks = [];
const errors = [];
const pass = name => { checks.push({ check: name, status: 'passed' }); console.log('PASS ' + name); };

async function login(page, role) {
  await page.goto(base);
  await page.getByLabel('账号', { exact: true }).fill(role);
  await page.getByLabel('密码', { exact: true }).fill('Laoyou123!');
  const response = page.waitForResponse(r => r.url().endsWith('/api/auth/login'));
  await page.getByRole('button', { name: '进入老友', exact: true }).click();
  assert.equal((await response).status(), 200, 'login must submit application/json');
  await page.locator('.side-nav').waitFor();
}

async function api(role, endpoint, data, method) {
  const session = await fetch(base + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: role, password: 'Laoyou123!' }) }).then(r => r.json());
  const response = await fetch(base + '/api' + endpoint, { method: method || (data ? 'POST' : 'GET'), headers: { Authorization: 'Bearer ' + session.token, 'Content-Type': 'application/json' }, ...(data ? { body: JSON.stringify(data) } : {}) });
  if (!response.ok) throw new Error(`API ${endpoint}: ${response.status}`);
  return response.json();
}

async function main() {
  const browser = await chromium.launch({ executablePath: 'D:/Chrome/Application/chrome.exe', headless: true,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
    downloadsPath: output, env: { ...process.env, TEMP: process.env.TEMP, TMP: process.env.TMP } });
  const elderContext = await browser.newContext({ viewport: { width: 1536, height: 1024 }, locale: 'zh-CN', permissions: ['camera', 'microphone'] });
  const childContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', permissions: ['camera', 'microphone'] });
  const elderPage = await elderContext.newPage();
  const childPage = await childContext.newPage();
  for (const page of [elderPage, childPage]) {
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', e => {
      if (e.type() !== 'error') return;
      if (e.location().url.includes('/snapshot') && e.text().includes('404')) return;
      errors.push(e.text() + ' @ ' + e.location().url);
    });
  }
  let reminderId;
  let elderId;
  try {
    await login(elderPage, 'elder');
    await elderPage.getByRole('heading', { name: '今日天气' }).waitFor();
    assert.equal(await elderPage.title(), '老友｜通通陪着您');
    assert.ok((await elderPage.locator('body').innerText()).includes('你好通通'));
    await elderPage.screenshot({ path: path.join(output, 'elder-home.png'), fullPage: true });
    pass('真实浏览器登录、主屏、天气和助手入口');

    const elders = await api('child', '/elders');
    elderId = elders[0].id;
    await elderPage.getByRole('button', { name: '用药提醒', exact: true }).click();
    await elderPage.getByRole('button', { name: '添加提醒', exact: true }).click();
    const title = '浏览器验收提醒-' + Date.now();
    await elderPage.getByLabel('提醒名称', { exact: true }).fill(title);
    await elderPage.getByLabel('药品名称', { exact: true }).fill('演示药物');
    await elderPage.getByLabel('剂量', { exact: true }).fill('演示剂量');
    await elderPage.getByLabel('每天时间', { exact: true }).fill('21:30');
    await elderPage.getByRole('button', { name: '保存提醒', exact: true }).click();
    await elderPage.getByText('提醒已添加。', { exact: true }).waitFor();
    reminderId = (await api('child', `/elders/${elderId}/reminders`)).find(item => item.title === title)?.id;
    assert.ok(reminderId, 'browser-created reminder must be readable by child API');
    await elderPage.screenshot({ path: path.join(output, 'reminder-created.png'), fullPage: true });
    pass('网页创建提醒并通过家属接口读回');

    await elderPage.getByRole('button', { name: '首页', exact: true }).click();
    await elderPage.getByRole('heading', { name: '今日天气' }).waitFor();
    await elderPage.setViewportSize({ width: 412, height: 915 });
    await elderPage.screenshot({ path: path.join(output, 'elder-mobile.png'), fullPage: true });
    const layout = await elderPage.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    assert.ok(layout.scroll <= layout.viewport + 2, 'elder screen must not overflow horizontally on mobile');
    pass('412px窄屏无横向溢出');
    await elderPage.setViewportSize({ width: 1536, height: 1024 });

    await login(childPage, 'child');
    await childPage.getByRole('heading', { name: '家属与社区', exact: true }).waitFor();
    await childPage.screenshot({ path: path.join(output, 'family-workspace.png'), fullPage: true });
    pass('家属工作台登录和数据读取');

    // Signaling is a real network connection; synthetic media avoids capturing
    // the user's real camera/microphone during automated acceptance.
    await elderPage.getByRole('button', { name: /你好通通/ }).click();
    await elderPage.getByPlaceholder('输入文字告诉通通').fill('发起视频通话');
    await elderPage.getByRole('button', { name: '发送', exact: true }).click();
    await elderPage.waitForURL(/\/call\//);
    const call = { id: new URL(elderPage.url()).pathname.split('/call/')[1] };
    // Deliberately join later to catch the dropped-offer regression.
    await elderPage.getByText(/等待/).first().waitFor({ timeout: 15000 });
    await childPage.getByRole('heading', { name: '家人视频来电', exact: true }).waitFor({ timeout: 15000 });
    await childPage.getByRole('button', { name: '接听', exact: true }).click();
    await elderPage.getByText('通话已接通', { exact: true }).waitFor({ timeout: 35000 });
    await childPage.getByText('通话已接通', { exact: true }).waitFor({ timeout: 35000 });
    const media = await childPage.locator('video').evaluateAll(elements => elements.map(v => ({ width: v.videoWidth, ready: v.readyState, stream: !!v.srcObject })));
    assert.ok(media.filter(v => v.width > 0 && v.ready >= 2 && v.stream).length >= 2, 'both local and remote decoded video must be present');
    await childPage.screenshot({ path: path.join(output, 'video-connected.png'), fullPage: true });
    await childPage.getByRole('button', { name: /挂断/ }).click();
    const ended = await api('elder', '/calls/' + call.id);
    assert.equal(ended.status, 'ended');
    await elderPage.getByText('通话已结束', { exact: true }).waitFor({ timeout: 8000 });
    pass('助手发起、首页来电接听、双端WebRTC解码、双方挂断状态');
    assert.equal(errors.length, 0, 'browser console errors: ' + errors.join('; '));
    pass('无浏览器运行时错误');
  } finally {
    if (reminderId) await api('child', '/reminders/' + reminderId, undefined, 'DELETE').catch(e => errors.push('cleanup: ' + e.message));
    await browser.close();
  }
}

main().then(() => { fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ passed: true, checks, errors }, null, 2)); })
  .catch(error => { console.error(error.message); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ passed: false, checks, errors, failure: error.message }, null, 2)); process.exitCode = 1; });

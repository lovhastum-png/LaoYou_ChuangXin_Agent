// 完整场景实测：登录老人屏 → 在首页上下文监听语音识别 → 外放粤语/普通话音频。
// 目的：确认「登录状态」是否影响识别，并打印真实识别内容。
// 用法：node tmp/probe_login_yue.cjs > 日志 2>&1
const { chromium } = require('playwright')
const { spawn } = require('child_process')
const path = require('path')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function playWav(name) {
  const file = path.resolve('tmp', name).replace(/\//g, '\\')
  return spawn(
    'powershell.exe',
    ['-NoProfile', '-Command', `(New-Object Media.SoundPlayer '${file}').PlaySync()`],
    { stdio: 'ignore' },
  )
}

const LISTEN = ([lang, ms]) =>
  new Promise((resolve) => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return resolve({ ok: false, reason: 'no-api' })
    const rec = new SR()
    const log = []
    rec.lang = lang
    rec.continuous = true
    rec.interimResults = true
    rec.maxAlternatives = 5
    const t0 = Date.now()
    const stamp = () => ((Date.now() - t0) / 1000).toFixed(1) + 's'
    rec.onstart = () => log.push(stamp() + ' onstart')
    rec.onsoundstart = () => log.push(stamp() + ' onsoundstart')
    rec.onspeechstart = () => log.push(stamp() + ' onspeechstart')
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        const alts = []
        for (let k = 0; k < r.length; k++) {
          alts.push({ t: r[k].transcript, c: Number(r[k].confidence.toFixed(2)) })
        }
        log.push(`${stamp()} [${r.isFinal ? 'FINAL' : 'interim'}] ${JSON.stringify(alts)}`)
      }
    }
    rec.onerror = (e) => log.push(stamp() + ' error:' + e.error)
    try { rec.start() } catch (e) { log.push('start-throw:' + String(e)) }
    setTimeout(() => {
      try { rec.abort() } catch {}
      resolve({ ok: true, log })
    }, ms)
  })

;(async () => {
  console.log('START probe_login_yue')
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: false,
    args: [
      `--proxy-server=${PROXY}`,
      '--proxy-bypass-list=127.0.0.1;localhost;<-loopback>',
      '--use-fake-ui-for-media-stream',
    ],
  })
  try {
    const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, permissions: ['microphone'] })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 160)))

    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 })
    await sleep(1200)
    await page.locator(':text("老人屏")').first().click({ timeout: 15000 })
    await sleep(500)
    await page.locator('#username').fill('elder')
    await page.locator('#password').fill('Laoyou123!')
    await page.locator('.login-submit').click()
    try {
      await page.waitForSelector('.wake-control button', { timeout: 30000 })
    } catch (err) {
      await page.screenshot({ path: 'tmp/login_fail.png' })
      const text = await page.evaluate(() => document.body.innerText)
      console.log('!! 未进入老人首页, 当前页面文本:\n' + text.slice(0, 600))
      throw err
    }
    console.log('>>> 已登录进入老人首页:', page.url())

    for (const [lang, wav, label] of [
      ['zh-HK', 'yue_wake.wav', '粤语 zh-HK'],
      ['zh-CN', 'cmn_wake.wav', '普通话 zh-CN'],
    ]) {
      console.log(`\n### ${label}  (音频 ${wav})`)
      const run = page.evaluate(LISTEN, [lang, 20000])
      await sleep(1800)
      console.log('   >>> 外放音频')
      const player = playWav(wav)
      const out = await run
      console.log('   ' + JSON.stringify(out.log, null, 2).split('\n').join('\n   '))
      if (!player.killed) player.kill()
      await sleep(800)
    }
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

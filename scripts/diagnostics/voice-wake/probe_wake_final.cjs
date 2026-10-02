// 最终验证：登录老人屏后，外放【放大版】音频，看识别能否出正确内容。
// 用法：node tmp/probe_wake_final.cjs <lang> <wav> [more...] > 日志 2>&1
const { chromium } = require('playwright')
const { spawn } = require('child_process')
const path = require('path')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 参数：lang wav lang wav ...
const CASES = []
for (let i = 0; i + 1 < process.argv.length - 2; i += 2) {
  CASES.push([process.argv[i + 2], process.argv[i + 3]])
}

function playWav(rel) {
  const file = path.resolve(rel).replace(/\//g, '\\')
  const script =
    `$w=New-Object -ComObject WScript.Shell;` +
    `1..20 | ForEach-Object { $w.SendKeys([char]175) };` +
    `Start-Sleep -Milliseconds 250;` +
    `(New-Object Media.SoundPlayer '${file}').PlaySync()`
  return spawn('powershell.exe', ['-NoProfile', '-Command', script], { stdio: 'ignore' })
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
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 })
    await sleep(1200)
    await page.locator(':text("老人屏")').first().click({ timeout: 15000 })
    await sleep(500)
    await page.locator('#username').fill('elder')
    await page.locator('#password').fill('Laoyou123!')
    await page.locator('.login-submit').click()
    await page.waitForSelector('.wake-control button', { timeout: 30000 })
    console.log('>>> 已登录老人首页')

    for (const [lang, wav] of CASES) {
      console.log(`\n### lang=${lang} wav=${wav}`)
      const run = page.evaluate(LISTEN, [lang, 20000])
      await sleep(1800)
      const player = playWav(wav)
      const out = await run
      console.log('   ' + out.log.join('\n   '))
      if (!player.killed) player.kill()
      await sleep(700)
    }
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

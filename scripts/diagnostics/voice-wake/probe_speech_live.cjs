// 诊断：真实麦克风 + 外放音频，直接监听 SpeechRecognition 的全部事件，看识别出什么。
// 用法：node tmp/probe_speech_live.cjs <wav> <lang> > 日志 2>&1
const { chromium } = require('playwright')
const { spawn } = require('child_process')
const path = require('path')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'
const WAV = path.resolve(process.argv[2] || 'tmp/yue_wake.wav').replace(/\//g, '\\')
const LANG = process.argv[3] || 'zh-HK'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function playWav(file) {
  return spawn(
    'powershell.exe',
    ['-NoProfile', '-Command', `(New-Object Media.SoundPlayer '${file}').PlaySync()`],
    { stdio: 'ignore' },
  )
}

;(async () => {
  console.log(`### 真实麦克风识别实测 lang=${LANG} wav=${WAV}`)
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
    const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, permissions: ['microphone'] })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 160)))
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })

    const run = page.evaluate(
      ([lang, ms]) =>
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
          rec.onaudiostart = () => log.push(stamp() + ' onaudiostart')
          rec.onsoundstart = () => log.push(stamp() + ' onsoundstart')
          rec.onspeechstart = () => log.push(stamp() + ' onspeechstart')
          rec.onspeechend = () => log.push(stamp() + ' onspeechend')
          rec.onsoundend = () => log.push(stamp() + ' onsoundend')
          rec.onresult = (e) => {
            for (let i = e.resultIndex; i < e.results.length; i++) {
              const r = e.results[i]
              const alts = []
              for (let k = 0; k < r.length; k++) {
                alts.push({ t: r[k].transcript, c: Number(r[k].confidence.toFixed(2)) })
              }
              log.push(`${stamp()} result[${r.isFinal ? 'FINAL' : 'interim'}] ${JSON.stringify(alts)}`)
            }
          }
          rec.onerror = (e) => log.push(stamp() + ' error:' + e.error + (e.message ? ' / ' + e.message : ''))
          rec.onend = () => log.push(stamp() + ' onend')
          try { rec.start() } catch (e) { log.push('start-throw:' + String(e)) }
          setTimeout(() => {
            try { rec.abort() } catch {}
            resolve({ ok: true, log })
          }, ms)
        }),
      [LANG, 22000],
    )

    await sleep(2000)
    console.log('>>> 外放音频')
    const player = playWav(WAV)
    const out = await run
    console.log('RESULT =', JSON.stringify(out, null, 2))
    if (!player.killed) player.kill()
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

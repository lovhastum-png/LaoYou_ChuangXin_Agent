// 探针：用 Chrome 假麦克风（--use-file-for-fake-audio-capture）播放 TTS 音频，
// 看页面里的 SpeechRecognition 能否真的识别出内容。
// 用法：node tmp/probe_speech.cjs <headless|headed> <wav绝对路径> <lang> > 日志 2>&1
const { chromium } = require('playwright')
const path = require('path')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'

const mode = process.argv[2] || 'headless'
const wav = path.resolve(process.argv[3] || 'tmp/yue_wake.wav').replace(/\\/g, '/')
const lang = process.argv[4] || 'zh-HK'

;(async () => {
  console.log(`### mode=${mode} wav=${wav} lang=${lang}`)
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: mode === 'headless',
    args: [
      `--proxy-server=${PROXY}`,
      '--proxy-bypass-list=127.0.0.1;localhost;<-loopback>',
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
      `--use-file-for-fake-audio-capture=${wav}`,
      '--autoplay-policy=no-user-gesture-required',
      '--disable-blink-features=AutomationControlled',
    ],
  })
  try {
    const ctx = await browser.newContext({
      viewport: { width: 1600, height: 1000 },
      permissions: ['microphone'],
    })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 160)))
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 })
    console.log('page ok:', await page.title())

    const out = await page.evaluate(
      ([recogLang, waitMs]) =>
        new Promise((resolve) => {
          const SR = window.SpeechRecognition || window.webkitSpeechRecognition
          if (!SR) return resolve({ ok: false, reason: 'no-speech-api' })
          let rec
          try {
            rec = new SR()
          } catch (e) {
            return resolve({ ok: false, reason: 'ctor:' + String(e) })
          }
          const log = []
          rec.lang = recogLang
          rec.continuous = true
          rec.interimResults = true
          rec.maxAlternatives = 5
          rec.onstart = () => log.push('onstart')
          rec.onaudiostart = () => log.push('onaudiostart')
          rec.onsoundstart = () => log.push('onsoundstart')
          rec.onspeechstart = () => log.push('onspeechstart')
          rec.onspeechend = () => log.push('onspeechend')
          rec.onresult = (e) => {
            for (let i = e.resultIndex; i < e.results.length; i++) {
              const r = e.results[i]
              const alts = []
              for (let k = 0; k < r.length; k++) alts.push(r[k].transcript)
              log.push(`result[${r.isFinal ? 'F' : 'i'}]:${JSON.stringify(alts)}`)
            }
          }
          rec.onerror = (e) => log.push('error:' + e.error + (e.message ? ' / ' + e.message : ''))
          rec.onend = () => log.push('onend')
          try {
            rec.start()
          } catch (e) {
            log.push('start-throw:' + String(e))
          }
          setTimeout(() => {
            try { rec.abort() } catch {}
            resolve({ ok: true, log })
          }, waitMs)
        }),
      [lang, 18000],
    )
    console.log('RESULT =', JSON.stringify(out, null, 2))
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

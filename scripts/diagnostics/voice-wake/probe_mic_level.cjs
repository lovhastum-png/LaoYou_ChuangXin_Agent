// 诊断：测量麦克风实际拾取到的电平，判断外放声音有没有进到浏览器。
// 用法：node tmp/probe_mic_level.cjs <wav> > 日志 2>&1
const { chromium } = require('playwright')
const { spawn } = require('child_process')
const path = require('path')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'
const WAV = path.resolve(process.argv[2] || 'tmp/yue_wake.wav').replace(/\//g, '\\')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function playWav(file) {
  return spawn(
    'powershell.exe',
    ['-NoProfile', '-Command', `(New-Object Media.SoundPlayer '${file}').PlaySync()`],
    { stdio: 'ignore' },
  )
}

const MEASURE = ([ms, useAec]) =>
  (async () => {
    const constraints = useAec
      ? { audio: true }
      : {
          audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
        }
    const stream = await navigator.mediaDevices.getUserMedia(constraints)
    const applied = stream.getAudioTracks()[0].getSettings()
    const ctx = new AudioContext()
    const src = ctx.createMediaStreamSource(stream)
    const an = ctx.createAnalyser()
    an.fftSize = 2048
    src.connect(an)
    const buf = new Float32Array(an.fftSize)
    const values = []
    const t0 = performance.now()
    while (performance.now() - t0 < ms) {
      an.getFloatTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
      values.push(Math.sqrt(sum / buf.length))
      await new Promise((r) => setTimeout(r, 50))
    }
    stream.getTracks().forEach((t) => t.stop())
    await ctx.close()
    const peak = Math.max(...values)
    const avg = values.reduce((a, b) => a + b, 0) / values.length
    return {
      appliedSettings: {
        echoCancellation: applied.echoCancellation,
        noiseSuppression: applied.noiseSuppression,
        autoGainControl: applied.autoGainControl,
      },
      peak: Number(peak.toFixed(5)),
      avg: Number(avg.toFixed(5)),
      samples: values.length,
    }
  })()

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
    const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, permissions: ['microphone'] })
    const page = await ctx.newPage()
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })

    for (const useAec of [true, false]) {
      const label = useAec ? '默认约束(含AEC)' : '关闭AEC/降噪/AGC'
      console.log(`\n### ${label}`)
      console.log('  静默基线 :', JSON.stringify(await page.evaluate(MEASURE, [2500, useAec])))
      console.log('  >>> 外放音频')
      const p = playWav(WAV)
      await sleep(300)
      console.log('  播放中   :', JSON.stringify(await page.evaluate(MEASURE, [3500, useAec])))
      await sleep(1200)
      if (!p.killed) p.kill()
    }
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

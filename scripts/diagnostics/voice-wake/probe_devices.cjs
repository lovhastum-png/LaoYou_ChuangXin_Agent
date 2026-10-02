// 探针：列出 Chrome 可见的音频输入设备，判断本机是否有可用麦克风。
const { chromium } = require('playwright')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROXY = 'http://127.0.0.1:7897'
const BASE = 'http://127.0.0.1:8000'

;(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: [`--proxy-server=${PROXY}`, '--proxy-bypass-list=127.0.0.1;localhost;<-loopback>'],
  })
  try {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      permissions: ['microphone'],
    })
    const page = await ctx.newPage()
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })
    const info = await page.evaluate(async () => {
      const out = { devices: [], error: null, getUserMediaError: null, trackLabel: null }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        out.trackLabel = stream.getAudioTracks().map((t) => t.label)
        stream.getTracks().forEach((t) => t.stop())
      } catch (e) {
        out.getUserMediaError = String(e.name) + ': ' + String(e.message)
      }
      try {
        const list = await navigator.mediaDevices.enumerateDevices()
        out.devices = list
          .filter((d) => d.kind === 'audioinput')
          .map((d) => ({ kind: d.kind, label: d.label, id: d.deviceId.slice(0, 12) }))
      } catch (e) {
        out.error = String(e)
      }
      return out
    })
    console.log(JSON.stringify(info, null, 2))
  } finally {
    await browser.close()
  }
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})

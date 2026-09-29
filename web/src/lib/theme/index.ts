/**
 * 外观设置运行时。
 *
 * 三层结构：
 *   1. 静态 CSS —— 12 个 [data-theme][data-mode] 令牌块，由构建脚本生成。
 *      没有 JS 也能正确渲染，因此不存在「首屏闪一下再变色」。
 *   2. 本模块 —— 把用户选择落到 <html> 的 data-* 属性与三个倍率变量上，并持久化。
 *   3. 自定义主色 —— 用户给一个颜色，本模块用与 tmp/theme_palette.py 完全相同的
 *      求解器在浏览器里现场反解出整套令牌，保证自选色同样满足 7:1。
 *
 * 为什么自定义主色要在运行时求解、而不是随便调个色？
 *   因为「用户选的主色」在浅色模式下必须承担正文和小字的着色，对比度下限是 7:1；
 *   而在深色模式下同一个色相要变亮才看得清，变亮之后又压不住按钮上的白字。
 *   所以不能直接用用户选的那个颜色，必须按对比度目标反解亮度，
 *   并把「前景角色」与「实底角色」拆成两组令牌。这正是 palette.generated.ts
 *   里 6 套预设主题的生成方式，这里把同一套算法搬到了浏览器。
 */
import {
  PALETTES,
  STATUS,
  THEME_ORDER,
  type Mode,
  type Palette,
} from './palette.generated'

export type { Mode, Palette }
export { THEME_ORDER }

/** 预设主题之外还有一个「自定义」伪主题，它不依赖静态 CSS，令牌全部内联求解。 */
export const CUSTOM_THEME = 'custom'

// ══════════════════════════════════════════════════════════════════════
// 可选档位
// ══════════════════════════════════════════════════════════════════════

export const FONT_TIERS = [
  { id: 'small', label: '小', hint: '16px 基准', scale: 0.9 },
  { id: 'standard', label: '标准', hint: '18px 基准', scale: 1 },
  { id: 'large', label: '大', hint: '20px 基准', scale: 1.12 },
  { id: 'xlarge', label: '特大', hint: '22px 基准', scale: 1.25 },
] as const

export const DENSITIES = [
  { id: 'compact', label: '紧凑', scale: 0.85 },
  { id: 'standard', label: '标准', scale: 1 },
  { id: 'roomy', label: '宽松', scale: 1.15 },
] as const

export const CORNERS = [
  { id: 'sharp', label: '直角', scale: 0.3 },
  { id: 'standard', label: '标准', scale: 1 },
  { id: 'round', label: '圆润', scale: 1.6 },
] as const

export const MODES = [
  { id: 'light', label: '浅色' },
  { id: 'dark', label: '深色' },
  { id: 'auto', label: '跟随系统' },
] as const

// ══════════════════════════════════════════════════════════════════════
// 布局 / 字体族 / 图形密度 —— 与配色、字号档位正交的三个维度
//
// 为什么这三项要单列、而不是塞进「主题」里一起打包？
//   因为它们解决的问题彼此无关：
//   · 左撇子要的是**操作手序**上的镜像，字号多大都改变不了这件事；
//   · 不识字的老人要的是**图标优先、弱化正文**，这是信息编码方式的问题，
//     不是把字放大就能解决的（字放大到极限，不识字仍然不识字）；
//   · 认知症老人可能被过多装饰图案干扰，所以「图形密度」必须能单独关掉，
//     不能被「选了个卡通主题」连带锁死。
//   做成独立维度后，家属可以按老人的实际情况逐项试，而不是被迫接受一整包。
// ══════════════════════════════════════════════════════════════════════

export const LAYOUTS = [
  { id: 'standard', label: '标准', hint: '常用布局' },
  { id: 'lefty', label: '左手优先', hint: '主要操作移到左侧' },
  { id: 'pictogram', label: '图形优先', hint: '大图标，弱化长文' },
] as const

export const FONT_FAMILIES = [
  { id: 'system', label: '系统默认', hint: '跟随本机' },
  { id: 'readable', label: '黑体', hint: '笔画均匀，远处易认' },
  { id: 'humanist', label: '人文衬线', hint: '字形有区别度' },
] as const

export const MOTIF_LEVELS = [
  { id: 'calm', label: '简洁', hint: '不放装饰图案' },
  { id: 'rich', label: '丰富', hint: '加卡通图案' },
] as const

export type LayoutId = (typeof LAYOUTS)[number]['id']
export type FontFamilyId = (typeof FONT_FAMILIES)[number]['id']
export type MotifId = (typeof MOTIF_LEVELS)[number]['id']

export type FontTierId = (typeof FONT_TIERS)[number]['id']
export type DensityId = (typeof DENSITIES)[number]['id']
export type CornerId = (typeof CORNERS)[number]['id']
export type ModeChoice = (typeof MODES)[number]['id']

export interface ThemePrefs {
  theme: string
  mode: ModeChoice
  font: FontTierId
  density: DensityId
  corner: CornerId
  /** 布局骨架：标准 / 左手优先 / 图形优先 */
  layout: LayoutId
  /** 字体族：系统 / 黑体 / 人文衬线 */
  fontFamily: FontFamilyId
  /** 装饰图案密度：简洁 / 丰富 */
  motif: MotifId
  /** 仅当 theme === CUSTOM_THEME 时生效 */
  customPrimary: string | null
}

export const DEFAULT_PREFS: ThemePrefs = {
  theme: 'azure',
  mode: 'light',
  font: 'standard',
  density: 'standard',
  corner: 'standard',
  layout: 'standard',
  fontFamily: 'system',
  motif: 'calm',
  customPrimary: null,
}

const STORAGE_KEY = 'laoyou.appearance.v1'

// ══════════════════════════════════════════════════════════════════════
// 主题展示信息（给设置界面用）
// ══════════════════════════════════════════════════════════════════════

export interface ThemeMeta {
  id: string
  name: string
  /** 浅色 / 深色两种模式下的主色，用于画色卡 */
  swatchLight: string
  swatchDark: string
  /** 色卡底 */
  surfaceLight: string
  surfaceDark: string
}

export const THEME_META: ThemeMeta[] = THEME_ORDER.map((id) => {
  const light = PALETTES[`${id}.light`]
  const dark = PALETTES[`${id}.dark`]
  return {
    id,
    name: light.name,
    swatchLight: light.tokens['--brand-solid'] ?? '#165198',
    swatchDark: dark.tokens['--brand-solid'] ?? '#1A579E',
    surfaceLight: light.tokens['--bg'] ?? '#FFFFFF',
    surfaceDark: dark.tokens['--bg'] ?? '#111418',
  }
})

// ══════════════════════════════════════════════════════════════════════
// 色彩工具 —— 与 tmp/theme_palette.py 逐一对应
// ══════════════════════════════════════════════════════════════════════

type Rgb = [number, number, number]

function hexToRgb(hex: string): Rgb {
  let h = hex.trim().replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ]
}

function rgbToHex(rgb: Rgb): string {
  return (
    '#' +
    rgb
      .map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0'))
      .join('')
      .toUpperCase()
  )
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

function srgbToLinear(c: number): number {
  const v = c / 255
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b)
}

export function contrast(a: string, b: string): number {
  const l1 = luminance(a)
  const l2 = luminance(b)
  const [hi, lo] = l1 < l2 ? [l2, l1] : [l1, l2]
  return (hi + 0.05) / (lo + 0.05)
}

/** hex → [h(0..1), l(0..1), s(0..1)] */
function rgbToHsl(hex: string): [number, number, number] {
  const [r0, g0, b0] = hexToRgb(hex)
  const r = r0 / 255
  const g = g0 / 255
  const b = b0 / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, l, 0]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  return [h / 6, l, s]
}

/** h(0..1), l(0..1), s(0..1) → hex */
function hslToHex(h: number, l: number, s: number): string {
  h = ((h % 1) + 1) % 1
  l = clamp01(l)
  s = clamp01(s)
  if (s === 0) return rgbToHex([l * 255, l * 255, l * 255])
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const chan = (t: number) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return rgbToHex([chan(h + 1 / 3) * 255, chan(h) * 255, chan(h - 1 / 3) * 255])
}

/** 色相/饱和度按「百分数」给，亮度按 0..1 给 —— 与 Python 侧签名一致 */
function hslDeg(hueDeg: number, satPct: number, light01: number): string {
  return hslToHex(hueDeg / 360, light01, satPct / 100)
}

function withL(hex: string, light01: number): string {
  const [h, , s] = rgbToHsl(hex)
  return hslToHex(h, light01, s)
}

/**
 * 固定色相与饱和度，二分亮度，使与 ref 的对比度刚好达到 target。
 * darken=true  → 越暗对比越高，返回满足条件的最亮值
 * darken=false → 越亮对比越高，返回满足条件的最暗值
 */
function solve(
  hueDeg: number,
  satPct: number,
  ref: string,
  target: number,
  darken = true,
  lo = 0,
  hi = 1,
  steps = 46,
): string {
  let best: number | null = null
  let a = lo
  let b = hi
  for (let i = 0; i < steps; i++) {
    const mid = (a + b) / 2
    const ok = contrast(hslDeg(hueDeg, satPct, mid), ref) >= target
    if (darken) {
      if (ok) {
        best = mid
        a = mid
      } else b = mid
    } else {
      if (ok) {
        best = mid
        b = mid
      } else a = mid
    }
  }
  return hslDeg(hueDeg, satPct, best ?? (darken ? 1 : 0))
}

/** 把 rgba(...) 前景合成到 bg 上，得到不透明 hex —— 校验半透明白字必须这样算 */
function over(fg: string, bg: string): string {
  const m = /rgba?\(([^)]+)\)/.exec(fg)
  if (!m) return fg
  const parts = m[1].split(',').map((s) => parseFloat(s))
  const [fr, fg2, fb] = parts
  const fa = parts.length > 3 ? parts[3] : 1
  const [br, bg2, bb] = hexToRgb(bg)
  return rgbToHex([
    fa * fr + (1 - fa) * br,
    fa * fg2 + (1 - fa) * bg2,
    fa * fb + (1 - fa) * bb,
  ])
}

function rgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// ══════════════════════════════════════════════════════════════════════
// 自定义主色 → 完整令牌集
// ══════════════════════════════════════════════════════════════════════

/** 叠在品牌面板上的白字阶梯，以及「面板最亮能到哪」的约束 */
const ON_BRAND_LEVELS: Array<[number, number]> = [
  [0.9, 7.0],
  [0.83, 7.0],
  [0.72, 4.5],
  [0.68, 4.5],
  [0.55, 3.0],
]

/** 品牌面板渐变的较亮端点：必须同时容得下上面这几档半透明白字 */
function solvePanel(hueDeg: number, satPct: number): string {
  let best: number | null = null
  let lo = 0.02
  let hi = 0.6
  for (let i = 0; i < 48; i++) {
    const mid = (lo + hi) / 2
    const p = hslDeg(hueDeg, satPct, mid)
    const ok = ON_BRAND_LEVELS.every(
      ([a, tgt]) => contrast(over(`rgba(255, 255, 255, ${a})`, p), p) >= tgt,
    )
    if (ok) {
      best = mid
      lo = mid
    } else hi = mid
  }
  return hslDeg(hueDeg, satPct, best ?? 0.2)
}

/** 品牌面板上那张纯白卡片内部的深色文字（与明暗模式无关） */
function nestedCard(hueDeg: number, satPct: number) {
  const white = '#FFFFFF'
  return {
    ink: hslDeg(hueDeg, 35, 0.11),
    muted: solve(hueDeg, 16, white, 7.72, true),
    brand: solve(hueDeg, satPct, white, 8.9, true),
  }
}

function baseSurfaces(mode: Mode, hue: number, sat: number) {
  if (mode === 'light') {
    return {
      bg: hslDeg(hue, Math.min(sat, 30) * 0.35, 0.98),
      surface: '#FFFFFF',
      surfaceSoft: hslDeg(hue, Math.min(sat, 30) * 0.42, 0.965),
      surfaceSunken: hslDeg(hue, Math.min(sat, 26) * 0.5, 0.94),
      surfaceSunkenDeep: hslDeg(hue, Math.min(sat, 26) * 0.55, 0.91),
      surfaceNeutral: hslDeg(hue, 10, 0.94),
      surfaceSelected: hslDeg(hue, Math.min(sat, 40) * 0.4, 0.96),
    }
  }
  return {
    bg: hslDeg(hue, Math.min(sat, 30) * 0.55, 0.075),
    surface: hslDeg(hue, Math.min(sat, 30) * 0.5, 0.115),
    surfaceSoft: hslDeg(hue, Math.min(sat, 30) * 0.5, 0.14),
    surfaceSunken: hslDeg(hue, Math.min(sat, 30) * 0.45, 0.06),
    surfaceSunkenDeep: hslDeg(hue, Math.min(sat, 30) * 0.4, 0.04),
    surfaceNeutral: hslDeg(hue, 10, 0.19),
    surfaceSelected: hslDeg(hue, Math.min(sat, 40) * 0.5, 0.17),
  }
}

/**
 * 由色相/饱和度 + 明暗模式反解出整套主题令牌。
 * 输出的键是 CSS 变量名，可直接 setProperty。
 */
export function buildCustomTokens(hue: number, sat: number, mode: Mode): Record<string, string> {
  const t: Record<string, string> = {}
  const S = STATUS[mode]
  // 注意：这里必须逐个映射成 CSS 变量名。曾经直接 Object.assign 过 baseSurfaces 的结果，
  // 但那边是 camelCase 键，于是后面所有 t['--surface'] 之类全是 undefined，
  // 整页在 luminance() 里崩成白屏 —— 已由 tmp/shot_themes.py 截图验证抓出。
  const surf = baseSurfaces(mode, hue, sat)
  t['--bg'] = surf.bg
  t['--surface'] = surf.surface
  t['--surface-soft'] = surf.surfaceSoft
  t['--surface-sunken'] = surf.surfaceSunken
  t['--surface-sunken-deep'] = surf.surfaceSunkenDeep
  t['--surface-neutral'] = surf.surfaceNeutral
  t['--surface-selected'] = surf.surfaceSelected

  // 状态色只随模式，不随主题
  t['--orange'] = S['--orange']
  t['--orange-soft'] = S['--orange-soft']
  t['--orange-solid'] = S['--orange-solid']
  t['--red'] = S['--red']
  t['--red-soft'] = S['--red-soft']
  t['--red-solid'] = S['--red-solid']
  t['--red-deep'] = S['--red-deep']
  t['--on-brand-error'] = S['--on-brand-error']
  t['--surface-alert'] = mode === 'light' ? hslDeg(33, 100, 0.975) : hslDeg(30, 42, 0.18)

  // 承载底口径：令牌必须解到它真实出现的那个底上，而不是默认白底。
  const carriers = [t['--surface'], t['--bg'], t['--surface-soft']]
  const pick = mode === 'light'
    ? (cs: string[]) => cs.reduce((a, b) => (luminance(a) <= luminance(b) ? a : b))
    : (cs: string[]) => cs.reduce((a, b) => (luminance(a) >= luminance(b) ? a : b))
  const hardText = pick(carriers)
  const hardLine = pick(carriers)

  if (mode === 'light') {
    t['--ink'] = hslDeg(hue, 35, 0.11)
    t['--muted'] = solve(hue, 16, hardText, 7.72, true)
    t['--muted-strong'] = solve(hue, 22, hardText, 8.6, true)
    t['--brand'] = solve(hue, sat, hardText, 7.88, true)
    t['--brand-deep'] = withL(t['--brand'], rgbToHsl(t['--brand'])[1] * 0.72)
    t['--brand-solid'] = t['--brand']
    t['--brand-deep-solid'] = t['--brand-deep']
    t['--brand-soft'] = hslDeg(hue, Math.min(sat, 70) * 0.72, 0.95)
    let bd = t['--brand-deep']
    while (contrast(bd, t['--brand-soft']) < 7.05) bd = withL(bd, rgbToHsl(bd)[1] * 0.94)
    t['--brand-deep'] = bd
    t['--brand-deep-solid'] = bd
    t['--line'] = solve(hue, 26, hardLine, 3.1, true)
    t['--line-strong'] = solve(hue, 30, hardLine, 4.05, true)
    t['--glass'] = rgba(t['--surface'], 0.52)
    t['--glass-strong'] = rgba(t['--surface'], 0.84)
    t['--modal-scrim'] = 'rgba(11, 24, 44, 0.35)'
    t['--shadow'] = `0 12px 30px ${rgba(t['--brand-deep-solid'], 0.055)}`
    t['--shadow-modal'] = '0 24px 70px rgba(0, 0, 0, 0.18)'
    t['--shadow-brand-sm'] = `0 8px 16px ${rgba(t['--brand-deep-solid'], 0.15)}`
    t['--shadow-brand-md'] = `0 12px 25px ${rgba(t['--brand-deep-solid'], 0.17)}`
  } else {
    t['--ink'] = hslDeg(hue, 20, 0.96)
    t['--muted'] = solve(hue, 14, hardText, 7.72, false)
    t['--muted-strong'] = solve(hue, 18, hardText, 8.6, false)
    t['--brand'] = solve(hue, Math.min(sat, 62), hardText, 7.88, false)
    t['--brand-deep'] = solve(hue, Math.min(sat, 55), hardText, 9.4, false)
    t['--brand-solid'] = solve(hue, Math.min(sat, 72), '#FFFFFF', 7.2, true)
    t['--brand-deep-solid'] = withL(t['--brand-solid'], rgbToHsl(t['--brand-solid'])[1] * 0.72)
    t['--brand-soft'] = hslDeg(hue, Math.min(sat, 60) * 0.5, 0.17)
    let bd = t['--brand-deep']
    while (contrast(bd, t['--brand-soft']) < 7.05) {
      bd = solve(hue, Math.min(sat, 55), t['--brand-soft'], 7.1, false)
    }
    t['--brand-deep'] = bd
    t['--line'] = solve(hue, 22, hardLine, 3.1, false)
    t['--line-strong'] = solve(hue, 26, hardLine, 4.05, false)
    t['--glass'] = rgba(t['--surface'], 0.72)
    t['--glass-strong'] = rgba(t['--surface'], 0.88)
    t['--modal-scrim'] = 'rgba(0, 0, 0, 0.62)'
    t['--shadow'] = '0 12px 30px rgba(0, 0, 0, 0.42)'
    t['--shadow-modal'] = '0 24px 70px rgba(0, 0, 0, 0.62)'
    t['--shadow-brand-sm'] = '0 8px 16px rgba(0, 0, 0, 0.38)'
    t['--shadow-brand-md'] = '0 12px 25px rgba(0, 0, 0, 0.45)'
  }

  // 品牌面板渐变端点 + 面板上的白色镶嵌卡片
  t['--brand-panel-to'] = solvePanel(hue, sat)
  t['--brand-panel-from'] = withL(t['--brand-panel-to'], rgbToHsl(t['--brand-panel-to'])[1] * 0.62)
  t['--brand-panel-hover'] = withL(t['--brand-panel-to'], rgbToHsl(t['--brand-panel-to'])[1] * 0.45)
  t['--on-brand'] = '#FFFFFF'
  t['--on-brand-surface'] = '#FFFFFF'
  const card = nestedCard(hue, sat)
  t['--on-brand-surface-ink'] = card.ink
  t['--on-brand-surface-muted'] = card.muted
  t['--on-brand-surface-brand'] = card.brand

  // 品牌透明度变体
  t['--brand-a13'] = rgba(t['--brand-solid'], 0.13)
  t['--brand-a25'] = rgba(t['--brand-solid'], 0.25)
  t['--brand-a45'] = rgba(t['--brand-solid'], 0.45)
  t['--brand-a48'] = rgba(t['--brand-solid'], 0.48)
  // 深色舞台上的品牌亮色
  t['--brand-on-dark'] = solve(hue, Math.min(sat, 70), '#061323', 4.6, false)

  // 自检：令牌集合必须与预设主题一模一样。
  // 这类「少一个键」的问题不会抛错，只会让某处颜色静默取不到值（或更糟、
  // 在 luminance() 里把整页搞崩），所以宁可留一段只在异常时才出声的检查。
  checkTokenSet(t)

  return t
}

/** 与预设主题比对令牌集合，缺项/多项都报出来（正常时不产生任何输出） */
function checkTokenSet(tokens: Record<string, string>) {
  const missing = CUSTOM_TOKEN_KEYS.filter((k) => !tokens[k])
  const extra = Object.keys(tokens).filter((k) => !CUSTOM_TOKEN_KEYS.includes(k))
  if (missing.length || extra.length) {
    console.error(
      '[theme] 自定义色板的令牌集合与预设不一致',
      missing.length ? { 缺失: missing } : {},
      extra.length ? { 多余: extra } : {},
    )
  }
}

/** 自定义主色的可校验摘要（设置界面用它显示「实测对比度」） */
export function describeCustom(hex: string, mode: Mode) {
  const tokens = buildCustomTokens(...hueSatOf(hex), mode)
  const carriers = [tokens['--surface'], tokens['--bg'], tokens['--surface-soft']]
  const worstText = Math.min(...carriers.map((c) => contrast(tokens['--brand'], c)))
  const worstMuted = Math.min(...carriers.map((c) => contrast(tokens['--muted'], c)))
  const onSolid = contrast('#FFFFFF', tokens['--brand-solid'])
  return {
    tokens,
    resolvedBrand: tokens['--brand'],
    brandSolid: tokens['--brand-solid'],
    worstText,
    worstMuted,
    onSolid,
  }
}

/** 从用户选的颜色里取出色相与饱和度（亮度一律重新求解，因为它是被对比度约束的那个量） */
function hueSatOf(hex: string): [number, number] {
  const [h, , s] = rgbToHsl(hex)
  return [h * 360, Math.max(18, Math.min(85, s * 100))]
}

/**
 * 状态语义冲突提示。
 *
 * 橙（待提醒）与红（异常/挂断）在本项目里承载固定语义，因此六套预设主题
 * 刻意避开了这两个色相。但自定义主色是自由的 —— 用户选一个砖红当主色时，
 * 主色会和「异常红」几乎分辨不出来，界面语义就糊了。
 * 这里只提示、不阻拦：用户的选择优先。
 */
const STATUS_HUES: Array<{ kind: 'orange' | 'red'; label: string; hue: number }> = [
  { kind: 'orange', label: '待提醒的橙色', hue: 30 },
  { kind: 'red', label: '异常的红色', hue: 8 },
]

export function semanticConflict(hex: string): { label: string; distance: number } | null {
  const [h, , s] = rgbToHsl(hex)
  // 低饱和度时色相没有意义（灰调不会和状态色打架）
  if (s < 0.12) return null
  const hue = h * 360
  let best: { label: string; distance: number } | null = null
  for (const item of STATUS_HUES) {
    const raw = Math.abs(hue - item.hue)
    const distance = Math.min(raw, 360 - raw)
    if (distance <= 24 && (!best || distance < best.distance)) {
      best = { label: item.label, distance }
    }
  }
  return best
}

// ══════════════════════════════════════════════════════════════════════
// 偏好读写
// ══════════════════════════════════════════════════════════════════════

export function loadPrefs(): ThemePrefs {
  if (typeof localStorage === 'undefined') return { ...DEFAULT_PREFS }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_PREFS }
    const parsed = JSON.parse(raw) as Partial<ThemePrefs>
    // 逐字段校验而不是整体信任：localStorage 里的值可能是旧版本写的
    // （例如本次新增 layout/fontFamily/motif 之前的记录没有这三个键），
    // 也可能是用户手改的。任何一项不合法就退回该项的默认值，
    // 而不是整条记录作废 —— 否则用户只是版本升级就会丢掉全部外观设置。
    return {
      theme: typeof parsed.theme === 'string' ? parsed.theme : DEFAULT_PREFS.theme,
      mode: MODES.some((m) => m.id === parsed.mode) ? (parsed.mode as ModeChoice) : DEFAULT_PREFS.mode,
      font: FONT_TIERS.some((f) => f.id === parsed.font) ? (parsed.font as FontTierId) : DEFAULT_PREFS.font,
      density: DENSITIES.some((d) => d.id === parsed.density)
        ? (parsed.density as DensityId)
        : DEFAULT_PREFS.density,
      corner: CORNERS.some((c) => c.id === parsed.corner) ? (parsed.corner as CornerId) : DEFAULT_PREFS.corner,
      layout: LAYOUTS.some((l) => l.id === parsed.layout)
        ? (parsed.layout as LayoutId)
        : DEFAULT_PREFS.layout,
      fontFamily: FONT_FAMILIES.some((f) => f.id === parsed.fontFamily)
        ? (parsed.fontFamily as FontFamilyId)
        : DEFAULT_PREFS.fontFamily,
      motif: MOTIF_LEVELS.some((m) => m.id === parsed.motif)
        ? (parsed.motif as MotifId)
        : DEFAULT_PREFS.motif,
      customPrimary: /^#[0-9a-fA-F]{6}$/.test(String(parsed.customPrimary))
        ? String(parsed.customPrimary)
        : null,
    }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function savePrefs(prefs: ThemePrefs) {
  try {
    const payload: Record<string, unknown> = { ...prefs }
    // 自定义主色的令牌是运行时算出来的，静态 CSS 里没有。
    // 这里把两种模式的结果一起存下来，好让 index.html 的内联脚本在首屏
    // 直接铺上 —— 否则自定义主题会先按默认蓝色画一帧再跳色。
    if (prefs.theme === CUSTOM_THEME && prefs.customPrimary) {
      const [hue, sat] = hueSatOf(prefs.customPrimary)
      payload.resolved = {
        light: buildCustomTokens(hue, sat, 'light'),
        dark: buildCustomTokens(hue, sat, 'dark'),
      }
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch {
    /* 隐私模式下写不进去也不该让界面崩掉 */
  }
}

/** 把 auto 解析成实际的 light/dark */
export function resolveMode(choice: ModeChoice): Mode {
  if (choice !== 'auto') return choice
  if (typeof matchMedia === 'undefined') return 'light'
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

const CUSTOM_TOKEN_KEYS = Object.keys(PALETTES['azure.light'].tokens)

function clearCustomOverrides(el: HTMLElement) {
  for (const key of CUSTOM_TOKEN_KEYS) el.style.removeProperty(key)
}

/**
 * 应用偏好。
 * 预设主题靠 data-* 属性命中静态 CSS；
 * 自定义主题靠内联令牌覆盖（内联样式优先级高于任何选择器）。
 */
export function applyPrefs(prefs: ThemePrefs) {
  const el = document.documentElement
  const mode = resolveMode(prefs.mode)

  el.dataset.theme = prefs.theme
  el.dataset.mode = mode
  // 布局 / 字体族 / 图案密度都靠 data-* 命中静态 CSS。
  // 做成属性而不是内联样式，是为了让样式表里能用后代选择器改结构（如左右镜像），
  // 内联样式只能改单个元素的属性，改不了兄弟顺序与网格列。
  el.dataset.layout = prefs.layout
  el.dataset.fontFamily = prefs.fontFamily
  el.dataset.motif = prefs.motif

  el.style.setProperty('--font-scale', String(fontScale(prefs.font)))
  el.style.setProperty('--density', String(densityScale(prefs.density)))
  el.style.setProperty('--radius-scale', String(cornerScale(prefs.corner)))

  clearCustomOverrides(el)
  let resolvedBg = PALETTES[`${prefs.theme}.${mode}`]?.tokens['--bg'] ?? '#F7F9FC'
  if (prefs.theme === CUSTOM_THEME && prefs.customPrimary) {
    const [hue, sat] = hueSatOf(prefs.customPrimary)
    const tokens = buildCustomTokens(hue, sat, mode)
    for (const [key, value] of Object.entries(tokens)) el.style.setProperty(key, value)
    resolvedBg = tokens['--bg'] ?? resolvedBg
  }

  // 顺手把地址栏/系统 UI 的颜色也调过来
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', resolvedBg)
}

export function fontScale(id: FontTierId): number {
  return FONT_TIERS.find((f) => f.id === id)?.scale ?? 1
}
export function densityScale(id: DensityId): number {
  return DENSITIES.find((d) => d.id === id)?.scale ?? 1
}
export function cornerScale(id: CornerId): number {
  return CORNERS.find((c) => c.id === id)?.scale ?? 1
}

/** 监听系统明暗切换；只在 mode === 'auto' 时重建令牌 */
export function watchSystemMode(onChange: (mode: Mode) => void): () => void {
  if (typeof matchMedia === 'undefined') return () => {}
  const mq = matchMedia('(prefers-color-scheme: dark)')
  const handler = () => onChange(mq.matches ? 'dark' : 'light')
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}

/**
 * 首屏防闪：这段逻辑与 index.html 里的内联脚本同源。
 * 内联脚本负责在 CSS 生效前就把 data-* 与三个倍率写上 <html>，
 * 这里导出一个等价的实现，供非 index.html 入口（如测试页）复用。
 */
export function bootstrapPrefs(): ThemePrefs {
  const prefs = loadPrefs()
  applyPrefs(prefs)
  return prefs
}

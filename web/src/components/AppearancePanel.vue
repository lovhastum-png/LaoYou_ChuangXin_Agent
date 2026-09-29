<script setup lang="ts">
/**
 * 外观设置面板。
 *
 * 四个维度都能即时生效、即时持久化，没有「确定/取消」的中间态 ——
 * 老人屏上最忌讳改了半天不知道改没改，所以点一下就是最终效果。
 *
 * 一处刻意的设计：主题色卡预览的是**该主题**的颜色，而不是当前主题的颜色。
 * 由于 CSS 变量是全局的，色卡必须在元素上内联写色，不能引用 var(--brand)。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Check, Palette, RotateCcw, X } from 'lucide-vue-next'
import {
  CORNERS,
  CUSTOM_THEME,
  DENSITIES,
  FONT_FAMILIES,
  FONT_TIERS,
  LAYOUTS,
  MODES,
  MOTIF_LEVELS,
  THEME_META,
  applyPrefs,
  cornerScale,
  densityScale,
  describeCustom,
  fontScale,
  loadPrefs,
  savePrefs,
  semanticConflict,
  watchSystemMode,
  type CornerId,
  type DensityId,
  type FontFamilyId,
  type FontTierId,
  type LayoutId,
  type Mode,
  type ModeChoice,
  type MotifId,
  type ThemePrefs,
} from '../lib/theme'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const prefs = ref<ThemePrefs>(loadPrefs())
const hexDraft = ref(prefs.value.customPrimary ?? '#165198')
const hexError = ref('')

/**
 * 「跟随系统」在界面上需要知道当前系统到底是深还是浅，
 * 而 matchMedia 不是响应式的，所以单独用一格状态承接变化事件。
 */
const systemDark = ref(
  typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches,
)
const mode = computed<Mode>(() => {
  if (prefs.value.mode !== 'auto') return prefs.value.mode
  return systemDark.value ? 'dark' : 'light'
})

/** 色卡用该主题在当前模式下的主色 */
function swatch(meta: (typeof THEME_META)[number]) {
  return mode.value === 'light' ? meta.swatchLight : meta.swatchDark
}

const custom = computed(() => {
  if (prefs.value.theme !== CUSTOM_THEME || !prefs.value.customPrimary) return null
  return describeCustom(prefs.value.customPrimary, mode.value)
})

/** 与「待提醒橙 / 异常红」色相过近时给一句提醒（只提示，不阻拦） */
const conflict = computed(() =>
  prefs.value.customPrimary ? semanticConflict(prefs.value.customPrimary) : null,
)

/**
 * 自定义色卡上要显示的颜色：始终显示**求解之后**的主色（按钮实底那一个），
 * 而不是用户输入的原值 —— 输入 #B4321E 时实际会用 #8E2718，
 * 直接显示原值会让人以为「我选的颜色没生效」。
 */
const customChipColor = computed(() => {
  const hex = prefs.value.customPrimary
    ?? (/^#[0-9a-fA-F]{6}$/.test(hexDraft.value) ? hexDraft.value : null)
  if (!hex) return 'var(--line-strong)'
  return describeCustom(hex, mode.value).brandSolid
})

function patch(next: Partial<ThemePrefs>) {
  prefs.value = { ...prefs.value, ...next }
}

function commitHex(value: string) {
  hexDraft.value = value
  if (/^#[0-9a-fA-F]{6}$/.test(value)) {
    hexError.value = ''
    patch({ theme: CUSTOM_THEME, customPrimary: value.toLowerCase() })
  } else {
    hexError.value = '请输入完整的 6 位十六进制色值，例如 #165198'
  }
}

/**
 * 点「自定义主色」时，把输入框里当前的颜色立刻落成真正生效的主色。
 * 不这么做会出现「面板里明明显示着 #165198，界面却一点没变」的状态 ——
 * 因为那时 theme 已是 custom 但 customPrimary 还是 null，没有任何令牌被铺上。
 */
function selectCustom() {
  commitHex(hexDraft.value)
  if (/^#[0-9a-fA-F]{6}$/.test(hexDraft.value)) patch({ theme: CUSTOM_THEME })
}

function reset() {
  prefs.value = {
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
  hexDraft.value = '#165198'
  hexError.value = ''
}

watch(
  prefs,
  (next) => {
    applyPrefs(next)
    savePrefs(next)
  },
  { deep: true },
)

let stopWatchSystem: (() => void) | null = null
onMounted(() => {
  // 系统明暗变化时：既更新「跟随系统」的判定，也重新求解自定义主色
  stopWatchSystem = watchSystemMode((m) => {
    systemDark.value = m === 'dark'
    if (prefs.value.mode === 'auto') applyPrefs(prefs.value)
  })
})
onUnmounted(() => stopWatchSystem?.())

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => props.open,
  (v) => {
    if (typeof window === 'undefined') return
    if (v) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)
onUnmounted(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
})

/** 字号样例：把当前档位的影响除掉，让四个样张之间的差异恰好等于档位差异 */
const curFont = computed(() => fontScale(prefs.value.font))
function fontSample(scale: number) {
  return `calc(1rem * ${scale} / ${curFont.value})`
}
const curDensity = computed(() => densityScale(prefs.value.density))
const curCorner = computed(() => cornerScale(prefs.value.corner))

/**
 * 每一档下面那句说明。
 *
 * 为什么要在界面上解释，而不是只放个名字？
 *   因为这三个维度的档位名（左手优先 / 图形优先 / 黑体）对家属来说
 *   并不自明 —— 装机的往往不是使用者本人。把「适合谁」写出来，
 *   家属才能对号入座，而不是靠猜。这也是这一整套设置存在的意义：
 *   它必须能被非专业的人正确使用。
 */
const layoutHint = computed(() => {
  switch (prefs.value.layout) {
    case 'lefty':
      return '适合习惯用左手操作的老人：通话、摄像头这些要点的入口都挪到左侧，拇指伸手就到；信息卡片仍按从左到右排，读起来不别扭。同时把可点区域的最小高度抬到 56px，减少误触。'
    case 'pictogram':
      return '适合识字很少、或看不进整句文字的老人家：图标放大成卡片主体，文字退到辅助位置，天气建议这类长句也会折叠成两行。认图比认字更省力，也更抗老化。'
    default:
      return '常用的排法：上面是天气和今天的提醒，中间是「你好通通」，下面的通话和摄像头左右各一个。'
  }
})

const fontFamilyHint = computed(() => {
  switch (prefs.value.fontFamily) {
    case 'readable':
      return '黑体笔画粗细均匀。老人视力下降时最先看不清的是很细的笔画，衬线字体的「横细竖粗」会让细的那一笔先糊掉，所以黑体在远处更稳。'
    case 'humanist':
      return '衬线字体靠笔画的轻重区分字形，「未／末」「天／夭」这类近形字更好分辨。适合还能看清细节的老人；如果视力已经比较差，选黑体更稳妥。'
    default:
      return '用本机自带的字体，不额外加载，页面打开最快。缺点是不同电脑上字的样子会略有差别。'
  }
})

const motifHint = computed(() => {
  switch (prefs.value.motif) {
    case 'rich':
      return '在卡片角落加一层淡淡的卡通纹样，界面看起来更活泼。纹样做得很浅，不会影响看字，也不会被误当成按钮。'
    default:
      return '不放任何装饰图案。如果老人容易被多余的花纹分散注意力，保持这一档。'
  }
})
</script>

<template>
  <div v-if="props.open" class="modal-backdrop" role="presentation" @click.self="emit('close')">
    <section
      class="modal-card appearance-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="appearance-title"
    >
      <header class="modal-header">
        <h2 id="appearance-title"><Palette :size="24" aria-hidden="true" />外观与阅读</h2>
        <button class="icon-button" type="button" aria-label="关闭" @click="emit('close')">
          <X :size="22" />
        </button>
      </header>

      <p class="modal-copy">
        这里的每一项都是立即生效的，选择会记在这台设备上。所有配色都按对比度目标预先校验过，
        换成任意主题后正文对比度仍不低于 7:1。
      </p>

      <!-- ── 配色主题 ── -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>配色主题</h3>
          <span>六套预设 + 自定义主色</span>
        </div>
        <div class="theme-grid">
          <button
            v-for="meta in THEME_META"
            :key="meta.id"
            class="theme-chip"
            :class="{ active: prefs.theme === meta.id }"
            type="button"
            :aria-pressed="prefs.theme === meta.id"
            @click="patch({ theme: meta.id })"
          >
            <span
              class="theme-chip-preview"
              :style="{ background: swatch(meta), color: 'var(--on-brand)' }"
              aria-hidden="true"
            >友</span>
            <span class="theme-chip-name">
              <span>{{ meta.name }}</span>
              <small>{{ mode === 'light' ? '浅色' : '深色' }}预览</small>
            </span>
            <Check v-if="prefs.theme === meta.id" class="theme-chip-check" :size="19" aria-hidden="true" />
          </button>

          <button
            class="theme-chip"
            :class="{ active: prefs.theme === CUSTOM_THEME }"
            type="button"
            :aria-pressed="prefs.theme === CUSTOM_THEME"
            @click="selectCustom()"
          >
            <span
              class="theme-chip-preview"
              :style="{ background: customChipColor, color: 'var(--on-brand)' }"
              aria-hidden="true"
            >自</span>
            <span class="theme-chip-name">
              <span>自定义主色</span>
              <small>系统自动配平对比度</small>
            </span>
            <Check
              v-if="prefs.theme === CUSTOM_THEME"
              class="theme-chip-check"
              :size="19"
              aria-hidden="true"
            />
          </button>
        </div>

        <div v-if="prefs.theme === CUSTOM_THEME" class="custom-color-row">
          <input
            type="color"
            :value="hexDraft"
            aria-label="选择主色"
            @input="commitHex(($event.target as HTMLInputElement).value)"
          />
          <input
            type="text"
            :value="hexDraft"
            maxlength="7"
            aria-label="主色十六进制值"
            @input="commitHex(($event.target as HTMLInputElement).value)"
          />
          <p class="custom-color-note">
            选一个颜色，系统会固定它的<b>色相</b>、重新解出<b>亮度</b>，
            让它在当前明暗模式下仍然满足正文 7:1、按钮白字 7:1。
            <template v-if="custom">
              实际使用的主色为 <strong>{{ custom.resolvedBrand }}</strong>。
            </template>
          </p>
        </div>

        <p v-if="hexError" class="form-error" role="alert">{{ hexError }}</p>

        <p v-if="conflict" class="notice-bar error" role="status">
          这个色相与「{{ conflict.label }}」非常接近（相差 {{ conflict.distance.toFixed(0) }}°）。
          主色和状态色长得一样的话，老人可能分不清「正常强调」和「需要留意」，建议换个色相
          —— 六套预设主题都刻意避开了橙红，就是这个原因。
        </p>

        <div v-if="custom" class="appearance-readout">
          <span class="readout-pill" :class="{ pass: custom.worstText >= 7 }">
            正文对比度最紧 {{ custom.worstText.toFixed(2) }}:1
          </span>
          <span class="readout-pill" :class="{ pass: custom.worstMuted >= 7 }">
            次要文字 {{ custom.worstMuted.toFixed(2) }}:1
          </span>
          <span class="readout-pill" :class="{ pass: custom.onSolid >= 7 }">
            按钮白字 {{ custom.onSolid.toFixed(2) }}:1
          </span>
        </div>
      </div>

      <!-- ── 明暗模式 ── -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>明暗模式</h3>
          <span>深色模式会把品牌色整体提亮，因此按钮实底另用一组更深的取值</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in MODES"
            :key="item.id"
            type="button"
            :class="{ active: prefs.mode === item.id }"
            :aria-pressed="prefs.mode === item.id"
            @click="patch({ mode: item.id })"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <!-- ── 字号档位 ── -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>字号档位</h3>
          <span>全站字号以 rem 计量，会整体缩放</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in FONT_TIERS"
            :key="item.id"
            type="button"
            :class="{ active: prefs.font === item.id }"
            :aria-pressed="prefs.font === item.id"
            @click="patch({ font: item.id })"
          >
            <span class="font-sample" :style="{ fontSize: fontSample(item.scale) }">老友</span>
            <small>{{ item.label }}</small>
          </button>
        </div>
      </div>

      <!-- ── 圆角与密度 ── -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>圆角</h3>
          <span>圆润一些边缘更柔和，直角则信息密度更高</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in CORNERS"
            :key="item.id"
            type="button"
            :class="{ active: prefs.corner === item.id }"
            :aria-pressed="prefs.corner === item.id"
            @click="patch({ corner: item.id })"
          >
            <span
              class="corner-sample"
              :style="{ borderRadius: `calc(var(--radius-md) * ${item.scale} / ${curCorner})` }"
              aria-hidden="true"
            />
            <small>{{ item.label }}</small>
          </button>
        </div>

        <div class="appearance-section-head" style="margin-top: var(--sp-22)">
          <h3>间距密度</h3>
          <span>只调间距，不动字号</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in DENSITIES"
            :key="item.id"
            type="button"
            :class="{ active: prefs.density === item.id }"
            :aria-pressed="prefs.density === item.id"
            @click="patch({ density: item.id })"
          >
            <span
              class="density-sample"
              :style="{ gap: `calc(var(--sp-4) * ${item.scale} / ${curDensity})` }"
              aria-hidden="true"
            >
              <i /><i /><i />
            </span>
            <small>{{ item.label }}</small>
          </button>
        </div>
      </div>

      <!-- ── 布局方式 ──
           这一档改的是「东西放在哪」，与字号、颜色都无关，所以单独成区。
           每一档都写清适用人群，因为家属通常不知道自己家老人该选哪个。 -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>布局方式</h3>
          <span>操作入口的位置会变，不影响配色</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in LAYOUTS"
            :key="item.id"
            type="button"
            :class="{ active: prefs.layout === item.id }"
            :aria-pressed="prefs.layout === item.id"
            @click="patch({ layout: item.id })"
          >
            <span class="layout-sample" :class="`layout-sample-${item.id}`" aria-hidden="true">
              <i /><i /><i />
            </span>
            <small>{{ item.label }}</small>
          </button>
        </div>
        <p class="appearance-hint">{{ layoutHint }}</p>
      </div>

      <!-- ── 字体 ── -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>字体</h3>
          <span>字的「好不好认」，和「多大」是两件事</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in FONT_FAMILIES"
            :key="item.id"
            type="button"
            :class="{ active: prefs.fontFamily === item.id }"
            :aria-pressed="prefs.fontFamily === item.id"
            @click="patch({ fontFamily: item.id })"
          >
            <span class="font-family-sample" :class="`ff-${item.id}`">永</span>
            <small>{{ item.label }}</small>
          </button>
        </div>
        <p class="appearance-hint">{{ fontFamilyHint }}</p>
      </div>

      <!-- ── 装饰图案 ──
           刻意与「布局」分开：需要大图标的老人未必需要装饰图案，
           把两者绑在一起会逼人二选一。 -->
      <div class="appearance-section">
        <div class="appearance-section-head">
          <h3>装饰图案</h3>
          <span>卡片角落的卡通纹样，可单独关闭</span>
        </div>
        <div class="segmented">
          <button
            v-for="item in MOTIF_LEVELS"
            :key="item.id"
            type="button"
            :class="{ active: prefs.motif === item.id }"
            :aria-pressed="prefs.motif === item.id"
            @click="patch({ motif: item.id })"
          >
            <span class="motif-sample" :class="`motif-${item.id}`" aria-hidden="true" />
            <small>{{ item.label }}</small>
          </button>
        </div>
        <p class="appearance-hint">{{ motifHint }}</p>
      </div>

      <div class="appearance-footer">
        <button class="secondary-button" type="button" @click="reset">
          <RotateCcw :size="19" aria-hidden="true" />恢复默认
        </button>
        <button class="primary-button" type="button" @click="emit('close')">完成</button>
      </div>
    </section>
  </div>
</template>

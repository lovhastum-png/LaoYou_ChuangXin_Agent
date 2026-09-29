import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
// 外观设置面板的样式单独成文件，因为 styles.css 是由
// tmp/apply_theme_system.py 全量重新生成的产物，人工维护的样式混进去会被覆盖。
import './styles/appearance.css'
import { bootstrapPrefs, watchSystemMode } from './lib/theme'

// index.html 里的内联脚本已在首屏写好 data-* 与三个倍率（防闪）。
// 这里再跑一次是幂等的，同时也负责铺上「自定义主色」那套运行时求解出来的令牌 ——
// 内联脚本只做属性与倍率，求解器塞不进那么小的内联段。
bootstrapPrefs()

// 「跟随系统」时，系统换了明暗要跟着重建令牌
watchSystemMode(() => bootstrapPrefs())

createApp(App).mount('#app')

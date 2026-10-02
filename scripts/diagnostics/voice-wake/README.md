# 语音唤醒诊断材料（voice-wake）

> 归档时间：2026-10-02
> 目的：验证浏览器端「语音唤醒」（`SpeechRecognition` / `webkitSpeechRecognition`）在本机与当前网络下能做到什么程度，**重点是粤语唤醒**。
> 完整结论与背景见 [`doc/03-子文档/33-语音唤醒诊断记录.md`](../../../doc/03-子文档/33-语音唤醒诊断记录.md)。

## 一、先看这几份日志

| 文件 | 内容 | 结论 |
|---|---|---|
| `logs/pf1.out` | ★ **关键证据**：把音频放大 5 倍并调高系统音量后外放，普通话完整识别成功（`你好通通今天天气怎么样`，置信度 0.90） | 路径可行 |
| `logs/pf_yue.out` | 粤语单独测试：`zh-HK` 能出结果，但内容是 `把整个调给删掉吗`（置信度 0.83），与音频内容无关 | 粤语不可靠 |
| `logs/pl4.out` | 登录老人首页后，粤语与普通话对照（含 `interim` 中间结果） | 对照参考 |
| `logs/probe_speech_h.log` / `_cmn.log` / `_headed.log` | 用 `--use-file-for-fake-audio-capture` 假麦克风播放音频，三轮均返回**空字符串**（不是网络错误） | 假麦克风不驱动识别 |
| `logs/probe_mic_level.log` | 麦克风电平：默认约束下外放时 `avg 0.012 → 0.045`（约 3.7 倍） | 收音正常 |
| `logs/probe_devices.log` | 本机音频输入设备枚举（Realtek 麦克风阵列） | 有真实麦克风 |

## 二、脚本

| 脚本 | 作用 |
|---|---|
| `probe_devices.cjs` | 列出 Chrome 可见的音频输入设备 |
| `probe_mic_level.cjs` | 测量麦克风拾取电平（对比 AEC 开 / 关） |
| `probe_speech.cjs` | 用假麦克风播放音频，监听 `SpeechRecognition` 全部事件 |
| `probe_speech_live.cjs` | 真实麦克风 + 外放，监听识别事件 |
| `probe_wake_final.cjs` | ★ 登录老人屏后外放（放大音频 + 提高音量），打印逐字识别结果 |
| `probe_login_yue.cjs` | 登录老人首页后，粤语 / 普通话对照测试 |
| `tts_yue_probe.py` | 用 edge-tts 生成粤语与普通话测试音频 |
| `check_dialect_tts.py` | 实测各方言 TTS 合成可用性（走 `/speech/synthesize`） |

## 三、复现方式

前提（本机实测环境，路径需按自己机器调整）：

- Node 22.22.2 与 Playwright（脚本里用绝对路径加载，需设 `NODE_PATH`）
- 系统 Chrome：`C:/Program Files/Google/Chrome/Application/chrome.exe`
- 后端已在 `http://127.0.0.1:8000` 运行（预览模式，演示账号 `elder` / `Laoyou123!`）
- 本机需可访问识别服务：实测**直连不可达，必须走代理**，脚本中写的是 `http://127.0.0.1:7897`

```bash
# 1. 起后端（另开一个窗口）
启动老友-预览.cmd

# 2. 生成测试音频（需要 backend/.venv 里的 edge_tts）
backend/.venv/Scripts/python.exe -X utf8 scripts/diagnostics/voice-wake/tts_yue_probe.py

# 3. 用 ffmpeg 把 mp3 转 16k 单声道 wav，并生成放大版
#    ffmpeg -i xxx.mp3 -ar 16000 -ac 1 -acodec pcm_s16le xxx.wav
#    ffmpeg -i xxx.wav -af "volume=5.0,alimiter=limit=0.95" xxx_loud.wav

# 4. 跑关键测试（会外放声音并自动调高系统音量）
export NODE_PATH="<playwright 的 node_modules 路径>"
node scripts/diagnostics/voice-wake/probe_wake_final.cjs zh-CN <放大后的普通话 wav>
node scripts/diagnostics/voice-wake/probe_wake_final.cjs zh-HK <放大后的粤语 wav>
```

## 四、待确认 / 待判断

1. **粤语识别是否还有别的配置路径**：`zh-HK` 在 Chrome 的 Web Speech API 下把粤语音频识别成完全无关的内容，是模型不支持、发音不匹配，还是拾音质量导致？是否值得换音色 / 换句式再试？
2. **普通话唤醒的稳定性**：单次成功（置信度 0.90）是否可重复？需要连跑多次统计成功率，才能判断能否作为演示或验收手段。
3. **是否需要虚拟声卡**：装 VB-Cable 之类把音频直接灌进录音设备，可绕开「外放 + 空气 + 内置麦克风」这条失真路径，值得评估。
4. **方言识别服务未配置**：`QWEN_API_KEY` / 讯飞凭据均为空，`/api/capabilities` 显示 `configured: false`，所以「粤语指令被助手理解」这条链路当前必然断在识别服务上，与本次唤醒测试是两件事。

## 五、注意

- 脚本会**外放声音并调高系统音量**（`SendKeys` 发送音量加键 20 次），跑之前请确认不会打扰他人；跑完请自行把音量调回。
- 所有脚本只读业务数据，只有 `probe_wake_final.cjs` / `probe_login_yue.cjs` 会以演示账号登录，不会改动任何记录。
- 音频样本来自 edge-tts 合成，无人声隐私内容。

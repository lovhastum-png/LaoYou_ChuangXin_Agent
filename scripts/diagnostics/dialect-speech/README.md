# 方言语音链路自检（dialect-speech）

这个目录用来回答一个问题：**「方言语音到底能不能用」**——而不是「配置项填了没有」。

## 为什么需要它

`GET /api/capabilities` 里的 `speech.configured=true` 只能说明后端**读到了**
`QWEN_API_KEY` 这个环境变量，它并不保证：

- Key 是有效的（无效会得到上游 401）
- 额度没超（超了会得到 429）
- `QWEN_ASR_MODEL` 指向的模型在当前账号下可用
- **后端是在填 Key 之前启动的**（最常见：先双击预览、后填 Key，capability 仍是 false）

前三条只能靠真打一次上游才知道；第四条是纯配置顺序问题，但只要不主动比对，
用户会以为是「这个功能坏了」。`selfcheck.py` 把这两件事都覆盖了。

## 用法

后端必须先跑起来（双击 `启动老友-预览.cmd`，或手工起 uvicorn）：

```bash
backend/.venv/Scripts/python.exe -X utf8 scripts/diagnostics/dialect-speech/selfcheck.py
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--base-url` | 默认 `http://127.0.0.1:8000`；也可指向 `http://127.0.0.1:8011` 这种第二实例 |
| `--elder-id` | 指定老人档案；默认取 `/api/elders` 的第一个 |
| `--user` / `--password` | 默认 `admin` / `Laoyou123!` |
| `--skip-synthetic` | 跳过需要 edge-tts + ffmpeg 的合成用例（不额外调用付费接口） |
| `--expect-configured` | 未配置方言识别时以退出码 **2** 结束，用于回归 |

退出码：`0` 全过 / `1` 有用例失败 / `2` 指定了 `--expect-configured` 但未配置。

## 它具体做了什么

1. **配置来源一致性**：把 `runtime/local.env` 里有没有 Key，和**运行中的后端**
   报告的状态对照。不一致就直说是「后端启动早于填 Key，请重启预览窗口」。
2. **未配置时的降级文案**：确认返回的是可读的 `503` 而不是 `500` 或挂住。
   已配置时会跳过（此时报错属于识别链路的问题，不该算作降级文案失败）。
3. **播报链路**：四个方言各打一次 `/speech/synthesize`，报告字节数与
   `X-Voice` 实际音色。四川话回落普通话是**预期行为**，脚本会明说而不是报错。
4. **识别链路**：用 `edge-tts` 合成一句已知的话 → `ffmpeg` 转成
   16k/单声道/PCM16（正是 `/speech` 要求的格式）→ 真实打过去 → 校验转录
   是否命中关键词。粤语用例带方言词（「脑瓜子」「点算」），用来观察归一化效果。
5. **上游失败定位**：Key 无效时会得到
   `HTTP 502：语音识别服务返回错误 401（dashscope.aliyuncs.com）`——
   把故障点明确指到授权层，而不是笼统的「识别失败」。

## 实测记录（2026-10-03）

- 工具链：`edge_tts 7.2.8` + `ffmpeg 6.0`（`D:/实验室项目/学生时代牌·工具/ffmpeg/ffmpeg.exe`）。
  注意本机**没有 ffprobe**。
- 未配置时（8000）：6 项全过。四个方言的降级文案、四条音色映射都符合预期：

  | 方言 | 实际音色 |
  | --- | --- |
  | `zh-CN` | `zh-CN-XiaoxiaoNeural` |
  | `yue-HK` | `zh-HK-HiuMaanNeural` |
  | `sichuan` | `zh-CN-XiaoxiaoNeural`（回落） |
  | `northeast` | `zh-CN-liaoning-XiaobeiNeural` |

- 用**假 Key** 在第二实例（8011）验证过「已配置」这条路：`configured` 翻成
  `true`、四个方言全部 `available`，随后 `/speech` 打到 dashscope 得到 401。
  这说明管路（`local.env` → 预览启动器 → 后端进程 → 上游）是通的，
  只剩下「换成真 Key」这一件事。
- **本机从未用真 Key 跑通过识别**，所以「转录准确率」「方言归一化质量」
  这两项结论目前是空的。填上真 Key 后重跑本脚本即可补齐。

## 坑

- 本机环境里注入了 `http_proxy=http://127.0.0.1:55640`，访问 `127.0.0.1`
  会被转发，代理对无服务端口返回 **502**，看起来像「后端挂了」。
  脚本里已经 `trust_env=False` 并清掉代理变量，手工用 curl 时要加 `--no-proxy`。
- `runtime/local.env` 不是完整 dotenv：只认 `^([A-Z_]+)=(.*)$`。
  写引号或行尾注释会被**原样**当成值的一部分（见 `scripts/localenv.py`）。

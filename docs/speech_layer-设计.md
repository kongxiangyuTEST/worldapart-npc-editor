# speech_layer · 说话表现层（可插拔模块）

> 独立、可插拔、零环境依赖的「说话表现」层。从 A1IndexTTSMod 抽离,只偷
> **数据契约**,不偷**生态**(不复用 BepInEx / TTS 引擎 / VTuber 适配器)。
> 放到任何 HTTP 转发链路(如 core_proxy)都能单独用,也能被别的工具直接 import。

**状态**: v0.1(2026-10-27)。作为独立包已建好并过单测,尚未接入 core_proxy
(接入动作属后续步骤,见文末「接入指南」)。

---

## 为什么做这个

游戏《不问凡尘》用 A1IndexTTSMod 让 NPC 有口音、情绪、语气,并配合数字人
朗读。但该 MOD 是 BepInEx IL2CPP 生态,和游戏版本强耦合,游戏一更新、
或组件失效,整体就不可用。

用户诉求是做一个**独立可插拔的东西**(不依赖各种环境)。逆向后发现 MOD 最有
复用价值的,不是它的生态,而是**纯数据契约**:

- 一帧说话的「风格」字段结构(`VoiceStyle`)
- 风格帧的文本编码/解码协议(`<a1tts_v1>{...}</a1tts_v1>` 信封)
- 说话口吻的轻量台账(32 个口吻档 → 58 种通用描述)

把这三样翻译成纯 Python、零依赖,就得到 speech_layer。它**不产生任何额外
延迟**(纯文本处理 0ms),也不会因为游戏更新失效。

---

## 目录

```
proxy/speech_layer/
├── __init__.py     # 对外统一入口 process()/has_frame()
├── contract.py     # VoiceStyle + Envelope 帧编解码(纯数据契约)
├── inject.py       # 把「要求模型输出风格帧」的提示词注入 system(幂等/可关)
├── talkback.py     # 模型漏输出台本时的兜底补写(从引号对白生成)
└── voicebook.py    # 32 口吻档 → 58 种通用中文口吻描述(轻量台账)
proxy/tests/
├── test_speech_contract.py   # 帧编解码 / VoiceStyle 校验
└── test_speech_inject.py     # 注入 / 兜底 / 台账
```

---

## 核心契约

### 1. VoiceStyle —— 一帧说话风格

```json
{
  "emotion_tags": ["轻笑"],   // 1~3 个,单个 ≤16 字
  "delivery": "节奏沉稳",     // 说话方式,≤80 字
  "intensity": 0.6            // 情绪强度,0.0~1.0
}
```

- `parse()` 做严格校验,失败抛 `ValueError` + 原因码(对齐原版)。
- `@dataclass(frozen=True)` 不可变,防意外改写。

### 2. Envelope —— 风格帧信封

模型在**正文末尾**追加一个风格帧:

```
她说:"你好。"<a1tts_v1>{"emotion_tags":["轻笑"],"delivery":"节奏沉稳","intensity":0.6}</a1tts_v1>
```

- `encode(content, style)`:把帧编码追加到正文末尾。
- `decode(content)`:解析出 `EnvelopeResult{content, style, status, failure}`。
- **容错**(对齐原版 StyleEnvelopeCodec):
  - 容忍标签大小写/空格/斜杠变体(`<A1TTS/v1>`)
  - 容忍 ```json``` 代码围栏、外层 JSON 转义(`\\"`)
  - 只取第一个帧;找不到/非法 → `status=invalid` 不抛异常,交上层决定

### 3. a1ttsv2 —— 台本兜底

模型输出里另有一个 `a1ttsv2` 字段(breeze2 引擎的朗读台本):
`{instruct, segments:[{text, tag}]}`。小参数模型常漏输出 → 按「宁多读不漏读」
从正文引号对白补一段。

---

## 对外 API

```python
import sys; sys.path.insert(0, "proxy")
from speech_layer import process, has_frame, VoiceStyle, Envelope, delivery_for

# 1) 注入侧(转发前):把风格帧要求写进第一条 system 末尾
req_json, injected_chars = process(req_json, {"tts_style_inject": True})
#    幂等:system 已有 A1_TTS_STYLE_V1 标记就不重复注入
#    可关:cfg["tts_style_inject"]=False 时完全跳过

# 2) 解码侧(回复到达后):解析正文里的风格帧
res = Envelope.decode(reply_content)
if res.status == "valid":
    print(res.content)        # 纯净正文(去掉帧)
    print(res.style)          # VoiceStyle or None

# 3) 台本兜底(回复到达后):模型漏输出 a1ttsv2 就从正文补
filled = fill_a1ttsv2(reply_inner_dict)   # True 说明补写了
```

---

## 设计取舍

| 取舍 | 说明 |
|---|---|
| **偷契约,不偷生态** | 只翻译 `VoiceStyle`+`Envelope`+台账,不绑 BepInEx/TTS/VTuber |
| **零依赖** | 只用标准库 `json`/`re`/`dataclasses`;测试用 `unittest` 不引 pytest |
| **零延迟** | 纯文本,0ms,**不经过** TTS 引擎;1-2s 是 LLM 推理+本地 TTS,与本层无关 |
| **可插拔** | 单一 `process()` 入口,可单独作为一个包被任何项目引用 |
| **幂等注入** | `A1_TTS_STYLE_V1` 标记防与下游 MOD/重复调用叠加 |
| **宁多读不漏读** | 台本兜底:无引号对白也去括号后整段当台词(沿用 core_proxy 决策) |
| **可关** | `tts_style_inject` 配置一次关闭,彻底不发风格帧要求 |
| **分帧优先** | V1 信封(行内风格帧)独立于 a1ttsv2(顶层字段),两协议互不混淆 |

---

## 测试

```bash
# 从项目根
.venv/Scripts/python.exe -m unittest discover -s proxy/tests -v
```

当前 57 个用例覆盖:

- **contract**:字段校验(缺 delivery/超长/数量越界/强度越界)、帧编解码往返、
  标签变体容错、```json``` 围栏、JSON 转义、只取第一帧、空/缺帧
- **inject**:模板存在、注入加标记、幂等、开关、无 system 跳过、空 system
- **talkback**:引号提取、多段、未闭合引号、无引号整段兜底、尊重已有字段(null 也尊重)
- **voicebook**:口吻档齐全、未知档回退默认、npc_name 不破坏

---

## 接入指南(后续步骤)

接入 core_proxy 时,把现有三处散落耦合换成 speech_layer 统一入口:

| 现有位置(core_proxy.py) | 换成 |
|---|---|
| `~1440-1465` 注入 TTS 风格提示词 | `process(req_json, cfg)` |
| `~2132-2163` 回复兜底补写 a1ttsv2 | `fill_a1ttsv2(inner)` |
| `~2201-2208` [TTS] 诊断日志 | `talkback_stats(inner)` |

接入是**替换实现、不动行为**——现有逻辑本就是 speech_layer 的"手写版",抽出来
是为了解耦、可复用、可单独测试。换完跑一遍上面的单测 + `web_smoke_test.js` 即可。

**注意**:接 core_proxy 会连带更新 README 的「语音 MOD 兼容」描述和版本历史,
建议放到下一次版本迭代一起做,不要中途热切。

---

## 知识库同步

引入设计说明与本文件一致,已新建独立文档 **「20 speech_layer 说话表现层」**
(挂在与「19 外部参考 · A1-TTS-MOD」同目录树):
`https://www.workbuddy.cn/space/d/WlHpI0LgIfSoTJBLG4AAyO`

## 实测记录(2026-10-27, Mac Ollama)

用 `speech_layer` 自身注入帧要求后直连 Mac Ollama `/api/chat` 冒烟:

- **`wa-defiant-ks`(Qwen3.5-9B)**:回复正常、人设对,但**漏吐风格帧**。
  这正是设计预判的"小参数模型常漏输出"失败模式 —— 也是 `talkback.py` 兜底存在的
  意义。注入侧本身 0 错误(2234 字模板含 `A1_TTS_STYLE_V1` 标记,格式正确)。
- **`wa-hauhau-q6`**:Mac 端推理慢,交互冒烟超时,未纳入结论。
- 结论:**模型侧不吐帧是已知失败模式,应靠兜底补写**而不是赌模型自觉；
  冒烟验证真正该测的是"帧编解码+兜底"这些纯逻辑,它们已由 57 个单测覆盖。

> 注:e2e 冒烟脚本留在 `proxy/tests/e2e_smoke.py`(不纳入 CI,只做手动验证用)。
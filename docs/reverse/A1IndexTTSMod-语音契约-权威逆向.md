# A1IndexTTSMod 语音契约 · 权威逆向资料

> 来源：本机 `H:\SteamLibrary\steamapps\common\A1\BepInEx\plugins\A1IndexTTSMod\A1IndexTTSMod.dll`（v0.8.2，7.85MB）内嵌资源，明文抽取。
> 抽取日期：2026-10-27。抽取脚本见本文末「附：抽取方法」。
> 本文件**是权威真相源**，此前所有基于推测的结论以本文件为准。

---

## 0. 一句话结论（先看这个）

MOD 的语音风格体系是**三层组合**，不是我们之前以为的「一帧三字段自由发挥」：

```
delivery = 角色基线文案（58 种，来自 actor_profile）
         + "；" +
           逐句表演文案（48 种，来自 performance_profile）
```

- **58 × 48 = 646 种实际组合**，4311 条台账条目全部落在这 646 种之内。
- **没有任何一条是模型自由编写的**。全部是 `offline_ai_authored` 预生成后按 key 查表。
- **`intensity` 最高只有 0.55**，中位 0.2。官方从不用高值。
- **`emotion_tags` 共 74 个去重词**，Top2 是「平静」(2187)、「谦和」(1946)，占 48%。

我们当前的 `voicebook.py` 只做了**第一层（58 条基线）**，第二层（48 种逐句表演）没做。这是真正缺的那一半。

---

## 1. 三份内嵌资源（可完整抽取）

| 资源 | 大小 | 内容 |
|---|---|---|
| `A1IndexTTSMod.a1ttsv2.schema.json` | 1546 B | V2 台本字段的 JSON Schema（权威） |
| `A1IndexTTSMod.npc-emotion-enhancement.zh-CN.md` | 2930 字 | **官方注入提示词全文**（V1 风格帧） |
| `A1IndexTTSMod.preset-voice-styles.zh-CN.json` | **4.07 MB** | **4311 条台账 + 1278 角色档案** |

另有磁盘上的 `prompts/a1ttsv2.contract.enc`（5304 B，`A1TTSV2CONTRACT1` 头 + Base64 + AES-GCM），
是「游戏字段说明 + 完整契约」的加密形态。**未解密**（不影响结论：字段定义已由 schema + 提示词覆盖）。

---

## 2. V2 台本 schema（权威原文）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "A1TTS v2 reply field value",
  "description": "Top-level a1ttsv2 value; null only when no speech. Merge into the game schema.",
  "anyOf": [
    {
      "type": "object",
      "additionalProperties": false,
      "required": ["instruct", "segments"],
      "properties": {
        "instruct": {
          "type": "string",
          "minLength": 1,
          "maxLength": 120,
          "description": "Overall delivery in Chinese, usually 20–40 characters; preserve speaker identity."
        },
        "segments": {
          "type": "array",
          "minItems": 1,
          "maxItems": 64,
          "description": "All spoken dialogue in order, without narration; one untagged segment for ordinary speech.",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["text", "tag"],
            "properties": {
              "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "description": "Dialogue only; preserve wording and punctuation. No narration or embedded tags."
              },
              "tag": {
                "type": "string",
                "maxLength": 6,
                "pattern": "^(|[\u3400-\u9fff]{2,6})$",
                "description": "Empty or 2–6 Han characters describing local audible delivery."
              }
            }
          }
        }
      }
    },
    { "type": "null" }
  ]
}
```

### 关键约束（与我此前方案的偏差）

| 项 | 真实值 | 我此前以为 |
|---|---|---|
| `instruct` 长度 | 1–120，**建议 20–40 字** | 未定 |
| `segments` 数量 | 1–**64** | 未定 |
| `segment.text` | 1–**4096** | 未定 |
| `segment.tag` | **空 或 2–6 个汉字**（正则 `^(|[\u3400-\u9fff]{2,6})$`） | 「35 个白名单枚举」❌ |
| `additionalProperties` | **false**（严格，多字段拒收） | 已知 |
| `a1ttsv2` 可为 null | **是**（无台词时） | 未知 |

> **重大修正**：`tag` **不是枚举白名单**，是「2–6 个汉字的开放描述」。
> 所以不存在「34 项/35 个 tag 的固定清单」这种东西——那 34 项是**演绎指导文档里给模型的示例规则**，不是解码器白名单。
> 这意味着：解码器接受任意 2–6 汉字 tag，**我们可以自由设计 tag 语义**，不必逐字对齐某个清单。

---

## 3. 官方 V1 注入提示词（全文）

文件：`docs/reverse/npc-emotion-enhancement.zh-CN.md`。核心结构：

```
## 当轮情感与语音表演补充 [A1_TTS_STYLE_V1]
（幂等标记）

一、先确定这一轮怎样回应对方
二、保留原 emotion，另给自然语言情感标签   ← 风格帧规则
三、输出约束
四、（输出示例）
```

### 与我们 `tts_style_prompt_v1.txt` 的差异（重要）

| 项 | 官方 | 我们 |
|---|---|---|
| emotion_tags 长度上限 | 未明说，示例最长「担忧中松了一口气」= 9 字 | `EMOTION_MAX = 16` |
| delivery 长度 | 「一句简短中文」 | `DELIVERY_MAX = 80` |
| intensity 语义 | **0.2 轻微 / 0.5 清楚可感 / 0.8 强烈** | 同（已对齐）|
| 帧位置 | content **字符串内部**台词末尾 | 同 |
| 幂等标记 | `[A1_TTS_STYLE_V1]` | 有 |
| **角色卡口吻优先级** | **明确写「下游不会再补上人物口吻预设」**，模型必须自己综合 | 我们无此句 |
| **无台词时** | 不生成帧 | 同 |

> 官方那句「下游不会再补上人物口吻预设；不要只输出脱离角色的情绪名称，也不要重复抄写整张角色卡」
> —— 正是我们「名片注入」要补的语义。这句应该抄进我们的注入模板。

---

## 4. 台账 `preset-voice-styles.zh-CN.json` 结构

```jsonc
{
  "schema_version": 1,
  "library_version": "2026-10-09.1",
  "language": "zh-Hans",
  "generation_method": "offline_ai_authored_per_utterance_decisions_with_character_card_profiles",
  "runtime_status": "embedded_default_enabled",
  "source_sha256": { ... 4 项 ... },
  "counts": {
    "base_greeting": 3373,
    "topic_opening": 781,
    "topic_opening_branch": 26,
    "persuade_opening": 131,
    "characters": 1278,
    "total_records": 4311
  },
  "characters": {            // 1278 个角色档案（角色基线）
    "100000": {
      "npc_name": "苏倾盏",
      "actor_profile": "hostess",
      "delivery": "中等语速，成熟从容，停顿有分寸",
      "card_sha256": "4bcc...",
      "personality": "廉贞：精明内敛",
      "topic_context_sha256": "5185...",
      "basis": "speakStyle"     // 或 "PersonalityDesc"
    }
  },
  "entries": [               // 4311 条逐句台账
    {
      "key": "base_greeting:100000:0",
      "npc_id": 100000,
      "npc_name": "苏倾盏",
      "source": "base_greeting",
      "source_id": "0",
      "language": "zh-Hans",
      "text": "外头的江风生冷，客官进来寻个避风的座儿吧。……",
      "text_sha256": "ec04...",
      "actor_profile": "hostess",
      "performance_profile": "welcome",
      "voice_style": {
        "emotion_tags": ["温和", "热情"],
        "delivery": "成熟从容地招呼客人，前句关照、后句带浅笑，"管够"说得笃定，语速适中。",
        "intensity": 0.3
      },
      "style_source": "offline_ai_authored",
      "speech_policy": "speak"
    }
  ]
}
```

### `matching` 字段（匹配规则，原文）

> `npc_id + exact trimmed text; active topic ID disambiguates identical text; conflicting performances remain unmatched; never match across characters or reuse previous-turn style`

即：**按 npc_id + 去空白后的精确文本匹配**；同样文本靠 topic ID 消歧；冲突则**宁可不匹配也不用错的**；绝不跨角色复用。

---

## 5. 三层结构实证（本文档最重要的一节）

### 5.1 第一层：58 种角色基线（`characters[].delivery`）

1278 个角色 → 去重后 **恰好 58 条**，中位长度 **16 字**。

按 `actor_profile` 分布（Top20）：

| profile | 人数 | profile | 人数 |
|---|---|---|---|
| leader | 101 | sun | 76 |
| hard | 86 | kind | 75 |
| patient | 85 | cold | 74 |
| subtle | 79 | ambitious | 73 |
| free | 79 | adaptive | 73 |
| clever | 79 | natural | 18 |
| bold | 78 | veteran | 17 |
| diplomat | 78 | official | 12 |
| sharp | 76 | wanderer | 10 |

> 我们的 `voicebook.py` 已收录全部 58 条，**这部分是对的、已完成**。

### 5.2 第二层：48 种逐句表演（`entries[].voice_style.delivery` 的「；」后段）

4311 条逐句 delivery → 去重 **646 条**，但拆开「；」后：
- 前段 → 59 种，其中 58 种与角色基线**完全一致**（第 59 种是变体）
- 后段 → **48 种表演模板**，中位长度 18 字

**后段 Top10（覆盖绝大多数）**：

| 次数 | 表演模板 |
|---|---|
| 1946 | 轻描淡写地自述，语气诚恳，不卑躬屈膝 |
| 979 | 平实介绍身份，前后语气连贯，收尾自然 |
| 146 | 重点落在所问之处，句尾轻微上扬，不连续逼问 |
| 125 | 只保留原有短鸣的情绪，不转换成人类对白 |
| 96 | 关心落在具体话上，轻柔询问，不演成训斥 |
| 95 | 边想边说，疑问前自然停顿，不故作高深 |
| 70 | 谈旧事稍放缓，转折留白，情绪收着说 |
| 69 | 先把事情说清，句间稍停，重音稳定 |
| 59 | 像迎接来客一样亲切招呼，邀请处语尾放松 |
| 51 | 追问有兴趣，节奏轻快，句尾自然上扬 |

完整 48 条见 `docs/reverse/performance-profiles.json`。

### 5.3 第三层：`emotion_tags`

8622 个 tag 实例 → 去重 **74 个词**。分布极度集中：

| tag | 次数 | tag | 次数 |
|---|---|---|---|
| 平静 | 2187 | 从容 | 108 |
| 谦和 | 1946 | 沉思 | 108 |
| 认真 | 380 | 关切 | 97 |
| 温和 | 365 | 愉悦 | 83 |
| 审慎 | 223 | 坦率 | 72 |
| 好奇 | 204 | 随性 | 72 |
| 坚定 | 191 | 怀念 | 70 |
| 自信 | 152 | 感慨 | 70 |
| 询问 | 147 | 直率 | 70 |
| 沉着 | 138 | 可靠 | 70 |

> **「平静」+「谦和」占了 48%**。官方风格是**极度克制**：绝大多数台词就是「平静/谦和」+ 低 intensity。
> 这与我们「让模型演得丰富」的直觉相反——**官方刻意不演**。

### 5.4 `intensity` 分布

```
n = 4311，min = 0.0，max = 0.55，中位 = 0.2
```

> **没有任何一条超过 0.55**。官方提示词说「0.8 为强烈；仅极端情境接近 1」，
> 但实际台账里**从来没用过 0.8**。这是「预设台词」的性质决定的（都是日常招呼/寒暄），
> 也说明：**高 intensity 应该留给真正的冲突场景，不该滥用**。

---

## 6. 解码器真实校验规则（从错误码还原）

DLL 里存在的失败原因码（原文）：

### V1 帧（`voice-style`）
```
expected_object                      unexpected_or_missing_fields
emotion_tags_must_be_array           emotion_tag_must_be_string
emotion_tag_empty_or_too_long        emotion_tag_count_out_of_range
delivery_must_be_string              delivery_empty_or_too_long
intensity_must_be_number             intensity_out_of_range
```

### V2 台本（`a1ttsv2`）
```
a1ttsv2_json_invalid                 a1ttsv2_json_truncated
a1ttsv2_too_long                     a1ttsv2_must_be_object_or_null
a1ttsv2_unexpected_or_missing_fields duplicate_a1ttsv2
no_spoken_line
instruct_must_be_string              instruct_blank
instruct_too_long
segments_must_be_array               segments_missing
segments_empty_after_filter
segment_count_out_of_range           segment_must_be_object
segment_unexpected_or_missing_fields
segment_text_must_be_string          segment_tag_must_be_string
segment_text_too_long
```

另有修复器状态：`repaired`、`alias_conflict:`、`duplicate_field:`、`nested_reply_outer_fields`。

> **`segments_empty_after_filter`** 这个错误码说明：MOD 会对 segments 做**过滤**（推测滤掉空 text / 纯旁白），全滤光则整帧失败。
> **`no_spoken_line`** 说明：没有可朗读台词时应当 `a1ttsv2 = null`，而不是空 segments。

---

## 7. 运行时配置（`BepInEx/config/org.a1indextts.mod.cfg`）

与语音风格相关的关键项：

| 配置 | 当前值 | 含义 |
|---|---|---|
| `Stage3Mvp.PresetVoiceStyles` | **true** | **启用内嵌角色情绪库**（即那 4311 条台账）|
| `Stage3Mvp.PromptEnhancement` | **true** | 给 NPC 回复注入 `voice_style` 要求 |
| `Stage3Mvp.OfficialLlmStrict` | false | false = schema 仅作指导；true = 严格解码 |
| `Stage3Mvp.TtsMode` | **v2** | v1=CosyVoice，**v2=Breeze** |
| `Stage3Mvp.Backend` | CosyVoiceAudioCpp | 后端名（v2 下走 Breeze URL）|
| `BreezeTtsUrl` | `http://127.0.0.1:8893/v1/audio/speech` | Breeze 端点 |
| `TtsUrl` | `http://127.0.0.1:8892/v1/audio/speech` | CosyVoice 端点 |
| `BreezeModelPath` | `...breeze-tts-2-q8_0.gguf` | 实际加载 q8_0 |
| `BreezeStreamingEnabled` | true | 流式播放 |
| `Stage2A.CaptureFullPrompt` | **false** | **可开启抓取完整 prompt/schema** |

### 面板截图印证

- 「朗读引擎」页：`实际运行 V2 / breeze2 / breeze-tts-2-q8_0.gguf`，**v1 CosyVoice 显示「未安装·不可选」**
- → **用户实际跑的是 Breeze V2 通道**，逐句 tag 这条路对用户**真实生效**。
- 「角色音色」页：`已应用参考音 柳秀娘 · npcs/100001.wav`，`其他匹配音色 1169 项`
- → 参考音是按 NPC 一一配好的，**音色层已经解决**，不是我们的问题域。

### `references/` 目录

```
references/
├── default_female.wav
├── default_male.wav
├── demo.wav
├── imported/
└── npcs/            ← 每个 NPC 一条参考音（面板显示 1169 项可选）
```

---

## 8. 情绪向量：`config/emotions.json`（另一套，别混）

```json
{
  "happy":    [0.8, 0,   0,   0,   0, 0, 0,    0],
  "angry":    [0,   0.7, 0,   0,   0, 0, 0,    0],
  "doubt":    [0,   0,   0,   0,   0, 0, 0.35, 0.25],
  "smile":    [0.35, 0,  0,   0,   0, 0, 0, 0.4],
  "think":    [0,   0,   0,   0,   0, 0, 0,    0.6],
  "sad":      [0,   0,   0.7, 0,   0, 0, 0,    0],
  "surprise": [0,   0,   0,   0,   0, 0, 0.7,  0],
  "afraid":   [0,   0,   0,   0.7, 0, 0, 0,    0],
  "normal":   [0,   0,   0,   0,   0, 0, 0,    0.55]
}
```

**这是 8 维情绪向量**，key 是游戏原生 `emotion` 枚举（9 类：happy/angry/doubt/smile/think/sad/surprise/afraid/normal）。

**注意区分**：
- 这个 8 维向量 → 喂给 TTS 引擎的**声学情绪控制**（v1 CosyVoice 路径的主要手段）
- V1 帧的 `emotion_tags` → **自然语言标签**，给 Breeze 的文本侧
- 两者**不是一回事**，不要混用

> 这也解释了官方提示词为什么强调「emotion 仍从本轮原有允许值中选择……不要把新自然语言标签写进 emotion」：
> 因为 `emotion` 要拿去查这张 8 维向量表，**写错就查不到**。

---

## 9. 我的判断修正清单

| # | 我之前的说法 | 真相 | 影响 |
|---|---|---|---|
| 1 | 「V2 有 35 个 audio tag 白名单」 | **错**。`tag` 是「空或 2–6 汉字」开放描述，无枚举 | 我们可自由设计 tag，不必对齐清单 |
| 2 | 「34 项声音事件规则是 tag 清单」 | **部分对**。34 项是给模型的**演绎指导示例**，不是解码器约束 | 可参考其语义，非强制 |
| 3 | 「V1 帧锁死三键所以不能加参数」 | **对**（`unexpected_or_missing_fields` 证实） | 保持三键 |
| 4 | 「V2 与引擎无关」 | **修正**：V2=Breeze 专用，v1=CosyVoice | 用户跑 v2，**可做** |
| 5 | 「做细粒度 tag 生成器」 | **需要重定义**：真正的缺口是**第二层 48 种表演模板**，而不是「打 tag」 | 方案重点转移 |
| 6 | 「intensity 可以到 0.8」 | 官方台账**最高 0.55**，中位 0.2 | 我们的注入应压低上限 |

---

## 10. 附：抽取方法（可复现）

内嵌资源是**明文**存在 DLL 里（虽名为 `.enc` 的那个在磁盘上是加密的，但 DLL 内嵌的三份资源都是明文 UTF-8）。

```python
d = open(r"...\A1IndexTTSMod.dll", 'rb').read()

# 1) schema：从 '{\r\n  "$schema"' 起做括号配平
i = d.find(b'{\r\n  "$schema"')

# 2) 提示词：从标题起，到下一个 \x00
i = d.find("# NPC 情感与语音表演增强提示词".encode('utf-8'))
j = d.find(b'\x00', i)

# 3) 台账：从 337640 起做括号配平（4.07MB，4311 条）
#    确认方法：json.loads 成功且 keys 含 'entries' / 'characters'
```

配平需正确处理 JSON 字符串内的 `{` `}` 和转义 `\"`。完整脚本思路见本节说明。

---

*本文件由逆向抽取生成，内容为 MOD 作者公开分发的数据，仅作接口兼容性研究使用。*

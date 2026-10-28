# TTS 链路验证 · NPC 语音可行性

> 2026-10-27 核实。本文回答一个问题：**让 NPC 出声，还差什么？**
> 结论全部有实测依据，不是推断。凡是推断都明确标注。

---

## 一、先纠正一条旧判定

知识库 18「与 SillyTavern 的能力对照与优化方向」的「**别碰**」象限写着：

> 群聊 / 多角色、**TTS**、图像生成、表情精灵、UI 主题——**游戏自己有，做了是重复建设**。

**这条判定是错的。** 依据：

| 检查项 | 结果 |
|---|---|
| 游戏音频库 `StreamingAssets/Audio` | 9683 个 `.bnk`，**没有配音库**（文件名是哈希，无 vo/voice/dialog 命名） |
| 59 个真实对话样本 | **0 个**含 `a1ttsv2` 或 `voice_style` |
| 样本 system 尾段 | 全是 `## 说话习惯`(28) / `## 角色背景`(21)，**没有语音段** |
| 用户确认 | 「游戏的 NPC 本身不会说话，只有普普通通的聊天功能」 |

**游戏本体没有 NPC 语音能力。** 当时大概是把「游戏有音频系统」误当成「有 NPC 语音」。

---

## 二、MOD 的接入方式（决定了一切）

MOD 用 **Harmony 补丁钩游戏进程内的 C# 方法**，不是 HTTP 层：

```
Game.Model.NpcModel.AddNpcChatMessage(
    System.String messageText,
    System.String npcRawOutput,
    System.Int32 promptTurnId)
```

其它钩子：
- `Game.Model.NpcModel.IsRepeatedChatReply`
- `OnPreGameReplyNormalization` (Prefix/Postfix)
- `JNGame.Ainpc.Prompt.ChatMode.OnHistoryNormalize` (Prefix/Postfix)
- `Game.Model.AinpcRuntime.LogResponse` / `LogRequest`
- `JNGame.Ainpc.Llm.LlmResult.OnLlmResponse` (Prefix/Postfix)
- `Game.NpcPersuadePanel.OnReceivePersuadeResponse`
- `Game.Model.ChatMessage.SendChatMessage`

**我们是在 HTTP 层中转，MOD 钩的是进程内方法。** 所以 MOD 的「提示增强」永远送不到我们的代理 —— 这是 59 个样本 0 命中的原因。

---

## 三、好消息：MOD 能读到我们的返回值

`AddNpcChatMessage` 的第二个参数是 **`npcRawOutput`**：模型输出的原始 JSON。

我们 HTTP 返回的 `choices[0].message.content`（那个 JSON 对象）最终会进到游戏，成为 `npcRawOutput`。**所以我们在返回里加 `a1ttsv2` 字段，MOD 理论上能读到。**

> ⚠ 这是**推断**（基于参数名语义），尚未实机验证。见第五节。

### 但还有一个坑：PromptEnhancement 没生效

`run-marker.json` 里有一行：

```json
"PromptEnhancementStatus": "ContextElevenArguments"
```

MOD 的提示增强读到的是**游戏自己的** ResponseFormat（11 个参数）。它加进去的 `a1ttsv2` 字段要求，走的是游戏内部的 prompt 构造，**不经过我们的代理**。

所以现状是：
- ✅ MOD 能读我们的返回（`npcRawOutput`）
- ❌ 我们收不到 MOD 的 prompt 增强

**结论：要出声，必须由我们的代理自己要求模型输出 `a1ttsv2`。** 不能指望 MOD 那边送要求过来。

---

## 四、决定性证据：从来没合成过一次

`last-error.json`（2026-10-25 16:11）：

```json
{
  "Stage": "v2_script",
  "ExceptionType": "Failure",
  "Summary": "操作失败",
  "Context": "缺少有效 a1ttsv2 台本，未从正文补写",
  "Scope": "npc-turn:npc:100221:1792944675970"
}
```

`audiocpp` 的 stdout 日志尾部：**只有 `/health` 心跳，没有一条 `/v1/audio/speech`**。

这三条互相印证：

```
没有 a1ttsv2 台本  →  MOD 不调 TTS  →  只有 /health 心跳  →  NPC 不出声
```

而 `a1ttsv2` 台本**只能由模型输出**，游戏不会给，MOD 也不会从正文补（它明确写了「未从正文补写」）。

---

## 五、要出声，缺的是这三步

| # | 缺什么 | 现在状态 | 怎么做 |
|---|---|---|---|
| 1 | **让模型输出 `a1ttsv2`** | 只注入了 V1 风格帧要求，没要求台本 | 写一份 V2 台本注入提示词，跟 V1 并排注入 |
| 2 | **台本兜底** | `talkback.py` 有，但 `tag` 恒为空、`instruct` 是写死的一句 | 用 `compose()` 生成 instruct，`tag` 按规则补 |
| 3 | **验证 MOD 能读到** | 未验证 | 开游戏说一句话，看 `.state/audiocpp` 有没有 speech 请求 |

第 1、2 步都是纯文本层，我们完全能做。

### 关于 `a1ttsv2` 的结构（权威，来自 schema）

```json
{
  "instruct": "整体演绎要求，中文，20–40 字，最长 120",
  "segments": [
    { "text": "台词，1–4096 字，不含旁白和标签",
      "tag": "空 或 2–6 个汉字" }
  ]
}
```

- `segments` 1–64 段
- `tag` 是**开放描述**（不是枚举白名单），正则 `^(|[\u3400-\u9fff]{2,6})$`
- `instruct` 只有 20–40 字，**正好能放我们 `compose()` 的产物**（但要截断）

---

## 六、优先级建议

**第 1 步最关键、成本最低**：只要模型吐出 `a1ttsv2`，NPC 就应该能出声（Breeze 模型、1170 条参考音、运行时全都齐了，只差台本）。

**第 2 步是保险**：小模型常有漏字段的毛病（知识库 20 已记「wa-defiant 回复正常但漏吐帧」），兜底能救回来。

**第 3 步是验证**：不做这一步，前面都是纸上谈兵。

---

## 七、待确认

- [ ] 实机验证：返回里带 `a1ttsv2` 后，MOD 是否真的合成（看 `.state/audiocpp/*.stdout.log` 有没有 `/v1/audio/speech`）
- [ ] `instruct` 用 `compose()` 的产物会不会超 120 字（58×36 组合最长 49 字，加上截断是安全的）
- [ ] `tag` 要不要按 `perceive()` 的判定填（如 `chat`→空、`sigh`→「轻叹」）

---

*依据文件：`docs/reverse/A1IndexTTSMod-语音契约-权威逆向.md`（schema / 台账 / 提示词全文）*


---

# 附：2026-10-27 深夜实测（Breeze 实机跑通）

## 一、TTS 能出真声音了

启动 audiocpp server（breeze2 后端，CUDA），用项目里的台本合成成功：

| 测试 | 文本 | 结果 |
|---|---|---|
| 长句 | 借宿？行。灶里刚添了柴，被窝也晒透了。进来。 | 6.16 秒 / 295KB / 4.4s 生成 |
| 短句 | 让开。别挡路。 | 1.20 秒 / 57KB / 1.4s 生成 |

输出规格：24000Hz / 单声道 / 16bit PCM。文件头 RIFF，可正常播放。

**准确说法：「Breeze 有能力把台本念出来」，不需要额外的 TTS 模型。**

⚠ **但这不等于「我们的台本会自动变成声音」**（2026-10-27 更正）：
上面这两段是我**用 Python 直接调 Breeze** 得到的。真实链路里没人做这个调用 ——
代理只能改写文本，**不能触发性朗读**。真正的调用方只有 MOD（`AddNpcChatMessage` 钩子里），
而它读的是**游戏侧**拿到的回复，不是我们直接喂给它的。

所以中间缺一个「搬运工」：我们的 `a1ttsv2` 必须经由游戏流传到 MOD 手里。
这条还没验证。

## 二、踩到三个坑（都已定位）

### 坑 1：`instruct` 放顶层会被静默忽略

同样一句话、不同 instruct：

```
顶层 instruct（错误写法）：
  平铺直叙 md5=67096316b2f4 1.28s
  极冷硬   md5=67096316b2f4 1.28s   ← 完全相同
  很激动   md5=67096316b2f4 1.28s   ← 完全相同
```

改到 `options.instruct` 后：

```
  平铺直叙 md5=5d091da5f707 1.36s
  难过     md5=1848c0fa8992 1.84s
  愤怒     md5=d58a01714f8e 1.52s
  轻声     md5=2669c9ae1733 1.92s
  唯一音频数 4/4 → instruct 生效
```

**确定性已验证**：同一 instruct 连跑两次 md5 完全一致（`0cda8e900e0c`），
所以 md5 不同确实代表语气不同，不是采样噪声。
（对照：`seed` 和 `guidance_scale` 也会改变输出，说明接口本身正常。）

### 坑 2：instruct 不能混进 input 文本

Breeze 会把说明文字当台词念出来：

```
纯台词                     1.28s
instruct + "。" + 台词      5.20s   ← 把说明也读了
instruct + "：" + 台词      4.88s
instruct + 换行 + 台词      4.88s
```

### 坑 3：必须给 `reference_text`

不给会报 `BreezeTTS clone requires reference_text`。
值在 `references/npcs/npc_id_name.csv` 的 `ref_text` 列（1591 条，与 MP3 参考音一一对应）。

另外：server 配置里的 `default_voice_preset` 指定了 `demo.wav`，
所以**不显式传 `voice_ref` 也无法进入纯 tts 模式**，一律走 clone。

## 三、启动服务的两个环境坑

1. **官方脚本默认模型路径不存在**：`Start-AudioCpp.ps1` 找
   `.cache/audiocpp/models/breeze-tts-2-q8_0.gguf`，实际文件在
   `.cache/audiocpp-breeze/models/`。必须加 `-ModelPath` 显式指定。
2. **代理变量冲突**：环境里同时有 `HTTPS_PROXY` 和 `https_proxy` 时，
   `Start-Process` 直接抛 `ArgumentException`。启动前要清掉这 6 个变量。

`tools/tts_probe.py start` 已经把这两条都绕开了（清变量 + 直接起 exe）。

## 四、还没解决的疑点（关键）

**MOD 的 TTS 请求体里没看到 `instruct` 键。**

从 DLL 字符串池提取到的请求键顺序是：

```
model / breeze2 / input / reference_text / response_format / wav / options / voice_ref
options 内部：guidance_scale / temperature / depth_temperature /
              top_p / top_k / max_tokens / text_chunk_size / text_chunk_mode / seed
```

`instruct` 不在其中。而 `a1ttsv2.instruct` 是 MOD 严格校验的字段
（有 `instruct_must_be_string` / `instruct_blank` / `instruct_too_long` 三个错误码），
说明它被解析了，但**是否真的传给 Breeze 未知**。

两种可能：
- **A**：MOD 通过别的代码路径把 instruct 塞进了 options（字符串池不含该实例），
  那我们写的 `a1ttsv2.instruct` 会生效；
- **B**：MOD 根本不传 instruct，只念 segments 的文本。
  那 NPC 能出声，但「语气」这一层白做。

**这个只能实机看**：开游戏说一句话，如果只听到平铺直叙、四种语气没区别，就是 B。

## 五、如果确认是 B，还有退路

我们完全可以**绕过 MOD 自己合成播放**：
Breeze 是标准的 HTTP 服务，`tools/tts_probe.py` 已经证明能直接调用。
难点只在「合成出来的 WAV 怎么送回游戏播放」——
这需要游戏侧有播放接口，比改文本难得多。

折中方案：先保证**出声**（segments 文本正确），语气层作为已知限制记录。


---

# 附：这条链到底谁在动手（2026-10-27 更正）

之前我说「台本 → 声音是通的」，**这个说法夸大了**。准确的分工是：

| 环节 | 谁在做 | 我们能不能控制 |
|---|---|---|
| 生成 `a1ttsv2` 台本 | 模型（被我们的注入要求） | ✅ 能 |
| 台本从代理送到游戏 | HTTP 返回体 | ✅ 能（写进返回 JSON） |
| 游戏内部把回复交给 MOD | **游戏自己** | ❌ 不能 |
| MOD 读台本 | MOD 的 `AddNpcChatMessage` 钩子 | ❌ 不能 |
| MOD 调 Breeze 合成 | MOD | ❌ 不能 |
| 播放音频 | MOD / 游戏 | ❌ 不能 |

**代理只能改写文本，不能触发朗读。** 我们做的一切都是「把数据放在它可能被拿到的位置上」，
至于 MOD 会不会拿、拿了会不会用，不在我们控制范围内。

我能直接调 Breeze 成功，只证明了「台本格式是 Breeze 认的」，
**没有**证明「游戏里会自动走通」。

## 决定性验证方法

MOD 自带诊断捕获（默认关闭）：

```ini
[Stage2A]
Enabled = false          # 改成 true
CaptureFullPrompt = false
CaptureDirectory = .state/stage2a
```

打开后跑一局游戏、跟 NPC 说句话，`.state/stage2a` 里会落一份记录，
其中 `ActualReplyJson` 字段就是 **MOD 实际收到的原始回复**。

- 如果它里面**有** `a1ttsv2` → 我们的字段穿过去了，MOD 有机会用
- 如果里面**没有**（只有 content/emotion 等游戏原生字段）→ 游戏在交给 MOD 之前
  就把我们的字段洗掉了，那么这条路走不通，只能考虑代理自己合成音频

这是唯一能定论的检查，静态分析看不出来。

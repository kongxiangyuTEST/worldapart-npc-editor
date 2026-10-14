# WorldApart 人物卡编辑器

一款给游戏《不问凡尘》（WorldApart）用的 AI 对话辅助编辑器。

它让你在游戏外编辑 NPC 的"用户名片"，并通过本地中转层把内容注入到游戏发给 AI 的提示词里，让游戏里 NPC 的对话体现出你写的设定。

---

## 主要功能

- 编辑 NPC 人物卡（用户名片、预设资料、原游戏背景）
- 用户名片注入，影响游戏里 NPC 的回复
- 撤销 / 恢复原版，改动随时回退
- 查看游戏实际发给 AI 的完整 prompt
- AI 提示词管理（INJECT 注入 + REPLACE 替换规则）
- 内置 Caddy + core_proxy 启停和日志
- 从游戏直接导出角色卡（不依赖 Mod）
- 从游戏资源里提取 NPC 头像
- 卡片对比（游戏更新后看新增 / 改动 / 删除）
- 合并改动（游戏字段用新版，用户名片保留）
- 上游地址切换（本地大模型 / 第三方 API）

---

## 工作原理

游戏发对话请求 → Caddy 8788 转发 → core_proxy 8789 拦截 → 提取 NPC 名字、读取对应 .md 卡片、把"用户名片"注入 prompt → 转发给上游（本地 8080 或第三方 API）→ AI 回复 → 游戏显示。

---

## 技术栈

- Python 3.10 + pywebview（桌面壳）
- Flask（core_proxy 代理）
- Caddy（转发层）
- 纯 HTML / CSS / JavaScript（前端，无框架）

---

## 快速开始

### 开发环境

1. 装 Python 3.10+
2. 双击 `setup.bat`（自动创建虚拟环境 + 装依赖）
3. 双击 `启动编辑器.bat`

### 打包发布

1. 双击 `build_editor.bat`
2. 双击 `build_core_proxy.bat`
3. 双击 `assemble.bat`
4. 产物在 `dist/WorldApartEditor/`

---

## 使用说明

见 `使用说明.txt`。

---

## 免责声明

本工具不修改游戏本体，只通过"AI 提示词中转层"影响对话结果。所有改动都是本地行为。使用风险自负。
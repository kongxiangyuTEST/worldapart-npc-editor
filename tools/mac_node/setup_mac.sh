#!/bin/bash
# ============================================================
#  把 Mac 变成局域网 LLM 节点（无 sudo 版）
#
#  2026-10-24 在 Mac mini M4 / macOS 15.1 / 16GB 上实测跑通。
#
#  为什么不用官方 install.sh：
#    它要 sudo。SSH 非交互环境下 sudo 输不了密码，会直接卡死。
#    而且它装的是 /Applications/Ollama.app，属于系统级。
#
#  为什么用 ollama-darwin.tgz 而不是 Ollama-darwin.zip：
#    zip 里是 .app（GUI），tgz 里是纯 CLI + dylib，解压即用，全程不碰系统目录。
#
#  用法：
#    本机直接跑：      bash setup_mac.sh
#    已有离线包：      bash setup_mac.sh /path/to/ollama-darwin.tgz
#    从 Windows 传：   scp /tmp/ollama-darwin.tgz nannl@<mac-ip>:~/
# ============================================================
set -e

TGZ_LOCAL="${1:-}"
INSTALL_DIR="$HOME/ollama"
PLIST="$HOME/Library/LaunchAgents/com.ollama.lan.plist"
RELEASE_URL="https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz"

echo ""
echo "════════ 一、硬件体检 ════════"
echo ""

CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "未知")
MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
echo "  芯片：$CHIP"
echo "  内存：${MEM_GB} GB"
echo "  磁盘：$(df -h / | awk 'NR==2{print $4}') 可用"
echo ""

# 按内存推荐。留 ~6GB 给系统和 KV cache。
if   [ "$MEM_GB" -ge 48 ]; then MODEL="qwen3:32b";      SLOT="离线对话也能用"
elif [ "$MEM_GB" -ge 32 ]; then MODEL="qwen3:32b-q4_K_M"; SLOT="离线对话可用，摘要质量很好"
elif [ "$MEM_GB" -ge 24 ]; then MODEL="qwen3:14b";      SLOT="摘要很好，短对话勉强"
else                            MODEL="qwen3:8b";       SLOT="摘要与记忆压缩足够，不建议跑主对话"
fi

echo "  推荐模型：$MODEL"
echo "  能做的事：$SLOT"
echo ""

echo "════════ 二、取安装包 ════════"
echo ""

mkdir -p "$INSTALL_DIR"

if [ -n "$TGZ_LOCAL" ] && [ -f "$TGZ_LOCAL" ]; then
  echo "  使用本地包：$TGZ_LOCAL"
  cp "$TGZ_LOCAL" "$HOME/ollama-download.tgz"
elif [ -f "$HOME/ollama-download.tgz" ]; then
  echo "  复用已下载的 ~/ollama-download.tgz"
else
  echo "  从 GitHub 下载（约 173MB）..."
  echo "  国内直连通常只有 100KB/s 左右，约 20 分钟。"
  echo "  如果太慢：在 Windows 上下好再 scp 过来，或用代理："
  echo "    curl -x http://127.0.0.1:7897 -L -o ollama-darwin.tgz \"$RELEASE_URL\""
  echo ""
  curl -L --max-time 3600 --retry 2 -o "$HOME/ollama-download.tgz" -sS \
    -w "  完成：%{size_download} 字节 / %{time_total}s（%{speed_download} B/s）\n" \
    "$RELEASE_URL"
fi

echo ""
echo "════════ 三、解压安装 ════════"
echo ""

TMPD=$(mktemp -d)
tar -xzf "$HOME/ollama-download.tgz" -C "$TMPD"

# ⚠️ 必须整包搬运，绝对不要挑文件（比如只搬 ollama + *.dylib）。
# 这个包里除了 ollama 还有：
#   llama-server    19MB，**推理引擎本体**，缺了它服务照样能起、/api/version 也正常，
#                   但一推理就 500，日志里是「llama-server binary not found」。
#   llama-quantize  量化工具
#   mlx_metal_v3/v4 版本号会随 Ollama 升级变化，硬编码目录名迟早踩空
# 2026-10-24 就是栽在挑文件上，白查了半天。
find "$TMPD" -mindepth 1 -maxdepth 1 -exec mv {} "$INSTALL_DIR"/ \;
rm -rf "$TMPD"

# 从网络来的二进制会被 Gatekeeper 打上隔离标记，清掉才能执行
xattr -dr com.apple.quarantine "$INSTALL_DIR" 2>/dev/null || true
chmod +x "$INSTALL_DIR/ollama" 2>/dev/null || true

# 装完自检：推理引擎在不在
if [ ! -x "$INSTALL_DIR/llama-server" ]; then
  echo ""
  echo "  [警告] 没找到 llama-server —— 推理会失败（HTTP 500）。"
  echo "         说明安装包结构变了，请把这段输出发我。"
  echo "         目录内容：$(ls "$INSTALL_DIR" | tr '\n' ' ' | cut -c1-200)"
fi

echo "  已装入 $INSTALL_DIR（$(ls "$INSTALL_DIR" | wc -l | tr -d ' ') 个文件）"
"$INSTALL_DIR/ollama" --version 2>&1 | head -1

echo ""
echo "════════ 四、配置开机自启 + 局域网监听 ════════"
echo ""

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ollama.lan</string>
  <key>ProgramArguments</key>
  <array>
    <string>__OLLAMA_BIN__</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key>
    <string>0.0.0.0:11434</string>
    <key>OLLAMA_FLASH_ATTENTION</key>
    <string>1</string>
    <key>OLLAMA_KV_CACHE_TYPE</key>
    <string>q8_0</string>
    <key>OLLAMA_KEEP_ALIVE</key>
    <string>30m</string>
    <key>OLLAMA_NUM_PARALLEL</key>
    <string>1</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>__LOG__</string>
  <key>StandardErrorPath</key>
  <string>__ERRLOG__</string>
</dict>
</plist>
PLISTEOF

# LaunchAgents 是用户级的，不需要 sudo
sed -i '' "s|__OLLAMA_BIN__|$INSTALL_DIR/ollama|" "$PLIST"
sed -i '' "s|__LOG__|$INSTALL_DIR/ollama.log|" "$PLIST"
sed -i '' "s|__ERRLOG__|$INSTALL_DIR/ollama.err.log|" "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "  说明：OLLAMA_HOST 必须改成 0.0.0.0 —— 默认只听 127.0.0.1，"
echo "       局域网里别的机器根本连不上，而且报错只会是"连接被拒绝"，很难查。"
echo "       OLLAMA_KEEP_ALIVE=30m 是为了让模型常驻，否则 5 分钟就卸载，"
echo "       下次请求要重新加载几十秒，游戏那边就是卡住。"
echo ""

sleep 4
echo "  自检：$(curl -s --max-time 5 http://127.0.0.1:11434/api/version || echo '未响应')"
echo ""

echo "════════ 五、拉模型（$MODEL）════════"
echo ""

ollama_bin="$INSTALL_DIR/ollama"
"$ollama_bin" pull "$MODEL"

echo ""
echo "════════ 完成 ════════"
echo ""
echo "  局域网地址：http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<本机IP>'):11434"
echo "  填进 Windows 侧 proxy/core_proxy_config.json 的 local_upstream_base_url"
echo "  （注意不要带 /v1，程序会自己拼）"
echo ""
echo "  还差一步（需要 sudo，请手动跑一次）："
echo "    sudo pmset -c sleep 0 displaysleep 10"
echo "  闲置 Mac 会自己睡，一睡服务就断，表现是 Windows 侧请求超时。"
echo ""

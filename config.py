import os
import json


# ==================== 动态路径 ====================
import sys
if getattr(sys, 'frozen', False):
    PROJECT_DIR = os.path.dirname(sys.executable)
else:
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


# ==================== 读用户配置 ====================
USER_CONFIG_PATH = os.path.join(PROJECT_DIR, "user_config.json")


def load_user_config():
    if not os.path.isfile(USER_CONFIG_PATH):
        print(f"[错误] 找不到 {USER_CONFIG_PATH}")
        print(f"[提示] 请创建它，内容：")
        print(f'  {{"game_dir": "你的游戏目录（WorldApart.exe 所在目录）"}}')
        return {}
    try:
        with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[错误] 读 user_config.json 失败: {e}")
        return {}


_user_config = load_user_config()
GAME_DIR = _user_config.get("game_dir", "")


# ==================== 路径配置 ====================
# 优先用 editor_cards（编辑器独立导出的卡片），没有再用 Mod 目录
_editor_cards = os.path.join(PROJECT_DIR, "editor_cards")
_mod_cards = os.path.join(GAME_DIR, "Mods", "WorldApartNpcCards") if GAME_DIR else ""

if os.path.isdir(_editor_cards) and any(f.endswith(".md") for f in os.listdir(_editor_cards)):
    CARDS_DIR = _editor_cards
elif _mod_cards and os.path.isdir(_mod_cards):
    CARDS_DIR = _mod_cards
else:
    CARDS_DIR = _editor_cards  # 默认，之后会由编辑器创建

WEB_DIR = os.path.join(PROJECT_DIR, "web")

INDEX_FILE = "NPC索引.md"
README_FILE = "使用说明.md"
IGNORED_FILES = {INDEX_FILE, README_FILE}

CARD_NAME_PREFIX_RE = r"^(\d+)-(.+)\.md$"


# ==================== 验证 ====================
if __name__ == "__main__":
    print("=" * 60)
    print("路径检查")
    print("=" * 60)
    print(f"项目目录: {PROJECT_DIR}")
    print(f"用户配置: {USER_CONFIG_PATH}")
    print(f"  存在: {os.path.isfile(USER_CONFIG_PATH)}")
    print(f"游戏目录: {GAME_DIR}")
    print(f"  存在: {os.path.isdir(GAME_DIR) if GAME_DIR else False}")
    print(f"卡片目录: {CARDS_DIR}")
    print(f"  存在: {os.path.isdir(CARDS_DIR) if CARDS_DIR else False}")
    print(f"前端目录: {WEB_DIR}")
    print(f"  存在: {os.path.isdir(WEB_DIR)}")
    print()

    if CARDS_DIR and os.path.isdir(CARDS_DIR):
        files = os.listdir(CARDS_DIR)
        md_files = [f for f in files if f.endswith(".md")]
        print(f"卡片目录里的 .md 文件总数: {len(md_files)}")
        print()
        print("前 10 个 .md 文件：")
        for f in md_files[:10]:
            print(f"  {f}")
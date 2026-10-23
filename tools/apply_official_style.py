"""
用游戏官方配置（tbnpcaipersona）补全角色卡的「用户名片 · 口吻」节。

⚠⚠ 重要（2026-10-24 验证，动手前必读）
--------------------------------------
游戏的 speakStyle **已经由 prompt 的「## 说话习惯」段自己注入了**——
拿真实样本比对过，400 字一字不差。所以：

    原样填进「用户名片」= 重复注入，纯浪费 token。

名片的正确用途是补**游戏没覆盖**的部分：行为逻辑（当…则…）、示例台词、
以及玩家自己的设定。口吻要写，也应该是**改写/微调**（游戏注入在前面，
我们的名片在最后、权重高，可以覆盖它），而不是复制。

本脚本因此默认不写入，需要显式 --with-style 才写，且会打印警告。
想参考官方口吻，用 --reference 生成独立文件（不碰卡片）。

数据质量（2026-10-24 核实，务必记住）
------------------------------------
- speakStyle：1156 条记录里 1040 条非空，但 distinct 只有 135 个
  → 是 135 种风格模板套给 1040 个 NPC，不是逐人定制
- attitudeByIntimacy：156 个 NPC × 7 档（档位 -1~5），每档中位 95 字
  → 这 156 个才是游戏认真做的
- 配置里有张冠李戴（如 npcId 111 / 121 都写着「可哥说话常带古韵…」）
  → 所以本脚本默认做「姓名校验」，风格文本里嵌了别的名字就跳过

用法
----
python tools/apply_official_style.py --dry-run 3        # 预览 3 张
python tools/apply_official_style.py --limit 20         # 只写 20 张（会自动备份）
python tools/apply_official_style.py --with-attitude    # 连亲密度分档一起写
python tools/apply_official_style.py --force            # 名片已有内容也覆盖（默认跳过）
"""
import argparse
import ast
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(PROJECT_DIR, 'editor_cards')
BACKUP_DIR = os.path.join(PROJECT_DIR, 'editor_cards_backup')
DEFAULT_PERSONA = r"E:\WorldApart_test\tables\tbnpcaipersona.json"

# 风格文本里常见的人名引用（用于张冠李戴检测）
NAME_HINT = re.compile(r'[“"]([^“”"]{1,4})[”"]\s*(?:说话|说道|说)')


def zh(v):
    """luban 多语言文本取简体"""
    if isinstance(v, dict):
        return v.get('zh-Hans') or ''
    return str(v) if v else ''


def parse_pairs(v):
    """attitudeByIntimacy 形如 [[档位, 文本], ...]"""
    try:
        return ast.literal_eval(v) if isinstance(v, str) else (v or [])
    except Exception:
        return []


def read_card(path):
    t = open(path, encoding='utf-8').read()
    m_id = re.search(r'NPC[_ ]?ID[：: ]*\s*(-?\d+)', t)
    m_name = re.search(r'[-·]\s*姓名[：: ]*\s*(.+)', t) or re.search(r'姓名[：: ]*\s*(.+)', t)
    return {
        'text': t,
        'npc_id': m_id.group(1) if m_id else None,
        'name': (m_name.group(1).strip() if m_name else ''),
    }


def card_style(text):
    """取「## 用户名片」下的正文（去掉注释）"""
    m = re.search(r'## 用户名片[^\n]*\n(.*?)(?=\n## )', text, re.S)
    if not m:
        return ''
    return re.sub(r'<!--.*?-->', '', m.group(1), flags=re.S).strip()


def mismatched(style, name):
    """风格文本里嵌了别的名字 → 张冠李戴，返回被提到的人名"""
    if not name:
        return None
    for hit in NAME_HINT.findall(style or ''):
        if hit and hit not in name and name not in hit:
            return hit
    return None


def build_block(style, attitude, with_attitude):
    lines = ['### 口吻', '', '（以下来自游戏官方设定，可直接修改）', '', style.strip(), '']
    if with_attitude and attitude:
        lines += ['### 亲密度分档态度', '']
        for lv, txt in attitude:
            body = zh(txt).strip() if isinstance(txt, dict) else str(txt).strip()
            if body:
                lines += [f'**{lv} 档**', '', body, '']
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--persona', default=DEFAULT_PERSONA, help='tbnpcaipersona.json 路径')
    ap.add_argument('--dry-run', type=int, nargs='?', const=3, default=0,
                    help='只预览，不写盘；可带条数')
    ap.add_argument('--limit', type=int, default=0, help='最多写多少张')
    ap.add_argument('--with-attitude', action='store_true', help='连亲密度分档一起写')
    ap.add_argument('--with-style', action='store_true',
                    help='允许写入官方口吻（默认关闭：游戏已注入「说话习惯」段，重复写是浪费）')
    ap.add_argument('--reference', metavar='OUT',
                    help='只生成官方口吻参考文件（不碰卡片），例如传一个 .md 路径')
    ap.add_argument('--force', action='store_true', help='名片已有内容也覆盖')
    ap.add_argument('--allow-name-mismatch', action='store_true', help='不做张冠李戴过滤')
    args = ap.parse_args()

    if not os.path.isfile(args.persona):
        print(f'找不到人格表：{args.persona}')
        print('先用 AssetStudio 导出 TextAsset，或改 --persona 指向它')
        return 1

    per_raw = json.load(open(args.persona, encoding='utf-8'))

    # 只出参考：不碰卡片
    if args.reference:
        groups = {}
        for x in per_raw:
            s = zh(x.get('speakStyle')).strip()
            if s:
                groups.setdefault(s, []).append(str(x.get('npcId')))
        out = ['# NPC 官方说话风格（去重）', '',
               f'非空 {sum(len(v) for v in groups.values())} 条 → 去重后 {len(groups)} 种',
               '', '> 游戏已通过「## 说话习惯」段注入，勿原样填进名片。', '']
        for i, (s, ids) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1])), 1):
            out += [f'## 风格 {i}（套用于 {len(ids)} 个 NPC）', '', s, '']
        open(args.reference, 'w', encoding='utf-8').write('\n'.join(out))
        print(f'已生成参考：{args.reference}（{len(groups)} 种，未改动任何卡片）')
        return 0

    if not args.with_style:
        print('默认不写入：官方口吻游戏已通过「## 说话习惯」段注入，原样填进名片是重复。')
        print('确要写入（例如想让名片覆盖游戏口吻）请加 --with-style。')
        print('只想参考：加 --reference <输出.md>')
        return 0

    per = {str(x.get('npcId')): x for x in per_raw}

    per = {str(x.get('npcId')): x for x in json.load(open(args.persona, encoding='utf-8'))}
    cards = sorted(glob.glob(os.path.join(CARDS_DIR, '*.md')))

    plan, skipped = [], {'no_id': 0, 'no_persona': 0, 'no_style': 0,
                         'has_style': 0, 'name_mismatch': 0}
    for path in cards:
        c = read_card(path)
        if not c['npc_id']:
            skipped['no_id'] += 1
            continue
        p = per.get(c['npc_id'])
        if not p:
            skipped['no_persona'] += 1
            continue
        style = zh(p.get('speakStyle')).strip()
        if not style:
            skipped['no_style'] += 1
            continue
        if card_style(c['text']) and not args.force:
            skipped['has_style'] += 1
            continue
        if not args.allow_name_mismatch:
            bad = mismatched(style, c['name'])
            if bad:
                skipped['name_mismatch'] += 1
                continue
        attitude = parse_pairs(p.get('attitudeByIntimacy')) if args.with_attitude else []
        plan.append((path, c, style, attitude))

    total = len(cards)
    print(f'卡片 {total} 张 → 计划写入 {len(plan)} 张')
    print('  跳过：无 ID %d / 无官方人格 %d / 官方无风格 %d / 名片已有内容 %d / 名字对不上 %d'
          % (skipped['no_id'], skipped['no_persona'], skipped['no_style'],
             skipped['has_style'], skipped['name_mismatch']))
    print()

    if args.dry_run:
        for path, c, style, attitude in plan[:args.dry_run]:
            print('=' * 66)
            print(os.path.basename(path), f"（NPC ID {c['npc_id']}）")
            print('=' * 66)
            print(build_block(style, attitude, args.with_attitude))
            print()
        print(f'[预览模式] 未写盘。共 {len(plan)} 张可写入。')
        return 0

    if args.limit:
        plan = plan[:args.limit]
    if not plan:
        print('没有可写入的卡片')
        return 0

    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dst, exist_ok=True)
    for f in glob.glob(os.path.join(CARDS_DIR, '*.md')):
        shutil.copy2(f, dst)
    print(f'已备份全部卡片 → {dst}')

    written = 0
    for path, c, style, attitude in plan:
        block = build_block(style, attitude, args.with_attitude)
        new = re.sub(r'(## 用户名片[^\n]*\n)(.*?)(?=\n## )',
                     lambda m: m.group(1) + '\n' + block + '\n',
                     c['text'], count=1, flags=re.S)
        if new == c['text']:
            new = c['text'].replace('## 用户名片', '## 用户名片\n\n' + block, 1)
        open(path, 'w', encoding='utf-8').write(new)
        written += 1
    print(f'已写入 {written} 张')
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""参考音电平归一化：统一响度并留出削波余量。

## 为什么

2026-10-30 实测：合成输出峰值跟随参考音峰值且放大（参考音 −1.7 dBFS → 输出 −3.1；
参考音 −7.8 → 输出 −12.4，参考音降 6.1 dB、输出降 9.3 dB）。
抽样 147 条参考音，32% 峰值高于 −3 dBFS，合成后顶到 0 dBFS 削波，
听感是「声音大时稍稍破音、混着人机噪」。

Breeze 不做限幅，MOD 链路改不了，所以只能在参考音上留余量。

## 做法（两步）

1. RMS 归一到 TARGET_RMS（统一响度，避免有的 NPC 天生偏轻）
2. 若峰值仍高于 PEAK_CEIL，再整体下压到 PEAK_CEIL（防削波，此时响度会低于目标）

只改振幅，不动采样率、位深、声道数、文件头。

## 用法

    python tools/normalize_references.py --dry-run    # 只统计，不写文件
    python tools/normalize_references.py --apply      # 备份后改写
"""
import argparse
import array
import glob
import math
import os
import shutil
import statistics
import sys
import wave

NPCS = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\references\npcs"
BACKUP = os.path.join(os.path.dirname(NPCS), "npcs_backup")

TARGET_RMS = -20.0     # dBFS
PEAK_CEIL = -3.0       # dBFS，留 3 dB 给合成抬升


def db(x):
    return 20 * math.log10(max(x, 1e-9))


def analysis(path):
    with wave.open(path) as w:
        if w.getsampwidth() != 2:
            return None
        ch, fs = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    mono = a[::ch] if ch > 1 else a
    if not mono:
        return None
    peak = max(max(mono), -min(mono)) / 32768
    rms = math.sqrt(sum(v * v for v in mono) / len(mono)) / 32768
    return {"ch": ch, "fs": fs, "n": len(mono),
            "peak_db": db(peak), "rms_db": db(rms),
            "bytes": raw}


def gain_for(st):
    """先按 RMS 归一，再按峰值封顶，取两者较小者，返回线性增益。"""
    g_rms = 10 ** ((TARGET_RMS - st["rms_db"]) / 20)
    # 峰值封顶：保证增益后峰值不超过 PEAK_CEIL
    g_peak = 10 ** ((PEAK_CEIL - st["peak_db"]) / 20)
    return min(g_rms, g_peak)


def apply_gain(path, st, g):
    a = array.array("h")
    a.frombytes(st["bytes"])
    if st["ch"] > 1:
        # 多声道按帧整体缩放，保持声道关系
        for i in range(len(a)):
            a[i] = max(-32768, min(32767, int(a[i] * g)))
    else:
        for i in range(len(a)):
            a[i] = max(-32768, min(32767, int(a[i] * g)))
    with wave.open(path, "wb") as w:
        w.setnchannels(st["ch"])
        w.setsampwidth(2)
        w.setframerate(st["fs"])
        w.writeframes(a.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.apply and not args.dry_run:
        sys.exit("加 --dry-run 先看统计，或 --apply 执行")

    files = sorted(glob.glob(os.path.join(NPCS, "*.wav")))
    print(f"参考音 {len(files)} 条")

    plan, skipped, already = [], 0, 0
    for p in files:
        st = analysis(p)
        if not st:
            skipped += 1
            continue
        g = gain_for(st)
        if abs(db(g)) < 0.3:
            already += 1
            continue
        new_peak = st["peak_db"] + db(g)
        new_rms = st["rms_db"] + db(g)
        plan.append((p, st, g, new_peak, new_rms))

    if plan:
        gains = [db(x[2]) for x in plan]
        pe = [x[3] for x in plan]
        print(f"需要处理 {len(plan)} 条（跳过 {skipped}，已达标 {already}）")
        print(f"增益：中位 {statistics.median(gains):+.1f} dB  "
              f"范围 {min(gains):+.1f} ~ {max(gains):+.1f} dB")
        print(f"处理后峰值：中位 {statistics.median(pe):.1f} dBFS  "
              f"最高 {max(pe):.1f}  "
              f">-3 的仍有 {sum(1 for x in pe if x > -3)} 条")
        up = sum(1 for x in gains if x > 0)
        print(f"变响 {up} 条 / 变轻 {len(gains)-up} 条")
    else:
        print("全部已达标，无需处理")

    if args.dry_run:
        print("\n（dry-run，未写文件）")
        return

    os.makedirs(BACKUP, exist_ok=True)
    print(f"\n备份到 {BACKUP}")
    done = 0
    for p, st, g, npk, nrm in plan:
        dst = os.path.join(BACKUP, os.path.basename(p))
        if not os.path.exists(dst):
            shutil.copy2(p, dst)
        apply_gain(p, st, g)
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(plan)}")
    print(f"完成 {done} 条")


if __name__ == "__main__":
    main()

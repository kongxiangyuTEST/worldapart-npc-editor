# -*- coding: utf-8 -*-
"""一条命令发版：改版本号 → 打包 → 压 zip → 传 GitHub Release。

用法：
    python tools/release.py 0.6              # 发 v0.6
    python tools/release.py 0.6 --no-build   # 已经打过包，只压+传
    python tools/release.py 0.6 --dry-run    # 只演练，不上传、不提交

注意：token 从 git 凭据读，不打印、不写进文件。
"""
import re
import os
import ssl
import sys
import json
import time
import zipfile
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.py")
DIST = os.path.join(ROOT, "dist", "WorldApartEditor")
REPO = "kongxiangyuTEST/worldapart-npc-editor"


def log(msg):
    print(msg, flush=True)


# ---------- 1) 改版本号 ----------
def set_version(ver):
    s = open(CONFIG, encoding="utf-8").read()
    new, n = re.subn(
        r'^APP_VERSION\s*=\s*"[^"]*"', f'APP_VERSION = "{ver}"', s,
        count=1, flags=re.M)
    if n == 0:
        raise SystemExit("config.py 里没找到 APP_VERSION")
    old = re.search(r'^APP_VERSION\s*=\s*"([^"]*)"', s, re.M).group(1)
    open(CONFIG, "w", encoding="utf-8").write(new)
    log(f"[版本] {old} → {ver}")
    return old


# ---------- 2) 打包 ----------
def build():
    log("[打包] 跑 build_all.bat（约 1-3 分钟）…")
    env = dict(os.environ, NO_PAUSE_STOP="1")
    r = subprocess.run(["cmd", "/c", "build_all.bat"], cwd=ROOT, env=env)
    if r.returncode != 0:
        raise SystemExit("[打包] 失败，看上面的输出")
    exe = os.path.join(DIST, "WorldApartEditor.exe")
    if not os.path.isfile(exe):
        raise SystemExit(f"[打包] 没找到 {exe}")
    log("[打包] 完成")


# ---------- 3) 压 zip ----------
def make_zip(ver):
    dst = os.path.join(ROOT, "dist", f"WorldApartEditor-v{ver}.zip")
    if os.path.exists(dst):
        os.remove(dst)
    t0 = time.time()
    n = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(DIST):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, DIST))
                n += 1
    mb = os.path.getsize(dst) / 1048576
    log(f"[压缩] {n} 个文件，{mb:.1f} MB，{time.time() - t0:.0f}s")
    return dst


# ---------- 4) 读凭据 ----------
def get_token():
    r = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=ROOT)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("拿不到 git 凭据 —— 先在终端跑一次 git push 完成认证")


# ---------- 5) 上传 ----------
def upload(ver, zip_path, dry):
    token = get_token()
    log(f"[凭据] 已读取（长度 {len(token)}，不打印）")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    hdr = {
        "Authorization": f"token {token}",
        "User-Agent": "wa-editor-release",
        "Accept": "application/vnd.github+json",
    }

    def call(url, method="GET", payload=None, raw=None,
             ctype="application/json"):
        h = dict(hdr)
        h["Content-Type"] = ctype
        data = raw if raw is not None else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload else None)
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with opener.open(req, timeout=900) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8")) if body else {}

    # 同 tag 已存在就先删（重跑时不冲突）
    try:
        old = call(f"https://api.github.com/repos/{REPO}/releases/tags/v{ver}")
        log(f"[发布] 已有 v{ver}，先删除旧 release")
        if not dry:
            call(f"https://api.github.com/repos/{REPO}/releases/{old['id']}",
                 method="DELETE")
    except Exception:
        log(f"[发布] 没有同名 release，新建")

    if dry:
        log(f"[演练] 跳过上传：{os.path.basename(zip_path)}")
        return f"https://github.com/{REPO}/releases/tag/v{ver}"

    body = open(os.path.join(ROOT, "RELEASE_NOTES.md"), encoding="utf-8").read() \
        if os.path.isfile(os.path.join(ROOT, "RELEASE_NOTES.md")) \
        else f"v{ver}"

    rel = call(f"https://api.github.com/repos/{REPO}/releases", method="POST",
               payload={"tag_name": f"v{ver}", "name": f"v{ver}",
                        "body": body, "draft": False, "prerelease": False})
    log(f"[发布] 已创建 {rel['html_url']}")

    with open(zip_path, "rb") as f:
        data = f.read()
    up = call(
        f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}"
        f"/assets?name={os.path.basename(zip_path)}",
        method="POST", raw=data, ctype="application/zip")
    log(f"[发布] 附件完成：{up['size'] / 1048576:.1f} MB")
    return rel["html_url"]


# ---------- 6) 提交 ----------
def commit(ver, dry):
    if dry:
        log("[演练] 跳过 git 提交与推送")
        return
    subprocess.run(["git", "add", "-A"], cwd=ROOT)
    subprocess.run(["git", "commit", "-m", f"release: v{ver}"], cwd=ROOT)
    env = dict(os.environ, GIT_SSL_NO_VERIFY="1")
    r = subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, env=env)
    if r.returncode != 0:
        log("[推送] 失败 —— 凭据过期？在终端跑一次 git push 认证")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    no_build = "--no-build" in sys.argv

    if not args:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]*)"',
                      open(CONFIG, encoding="utf-8").read(), re.M)
        ver = m.group(1)
        log(f"[版本] 没传版本号，用当前 {ver}")
    else:
        ver = args[0].lstrip("vV")

    log(f"=== 发布 v{ver}{'（演练）' if dry else ''} ===")
    old = set_version(ver)
    if not no_build:
        build()
    zip_path = make_zip(ver)
    url = upload(ver, zip_path, dry)
    commit(ver, dry)
    log("")
    log(f"完成：{url}")
    if old == ver and not no_build:
        log("提示：版本号没变，下次记得传新版本号（如 release.bat 0.7）")


if __name__ == "__main__":
    main()

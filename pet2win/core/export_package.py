"""照翻 Pet2/Core/ExportPackage.swift：后端 --export 打好 zip 以后，往同一个 zip 里追加桌宠自己的文件：
<zip 顶层文件夹>/pet2/manifest.json、consent.json、profile.json、setup.json、calibrations/*.json、events.jsonl。
Mac 版用 /usr/bin/zip 追加；这里用 Python 自带的 zipfile 追加，不重写后端的文件。只用标准库。"""
import datetime
import json
import os
import time
import zipfile

PET2_VERSION = "0.2.0"


class ExportFailure(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def top_folder(entries, zip_name):
    """zip 里的顶层文件夹：所有条目共同的第一段；找不到用 zip 名字（去掉 .zip）。"""
    fallback = os.path.splitext(zip_name)[0]
    firsts = set()
    for e in entries:
        first = next((p for p in e.split("/") if p), None)   # Swift 的 split 不要空段
        if first:
            firsts.add(first)
    if len(firsts) == 1:
        only = next(iter(firsts))
        if all(e.startswith(only + "/") for e in entries):
            return only
    return fallback


def _json_bytes(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")


def pet2_files(data, exported_at, tz=None):
    """要追加的文件：[(相对 pet2/ 的路径, 内容 bytes)]。exported_at 是 Unix 秒；tz 是 tzinfo（默认本机时区）。"""
    files = []
    if tz is None:
        when = datetime.datetime.fromtimestamp(exported_at).astimezone()
    else:
        when = datetime.datetime.fromtimestamp(exported_at, tz)
    consent = data.consent
    files.append(("manifest.json", _json_bytes({
        "app": "pet2",
        "pet2_version": PET2_VERSION,
        "exported_at": when.strftime("%Y-%m-%dT%H:%M:%S%z"),   # 和后端 manifest 的 exported_at 一样
        "consent_version": consent.version,
    })))
    # consent.json：原样 + 顶层 time（合并脚本拿它算第 0 天）。
    obj = consent.to_json()
    if consent.current is not None:
        obj["time"] = consent.current.time
    files.append(("consent.json", _json_bytes(obj)))
    health = consent.current is not None and consent.current.c3 is True
    profile = data.profile
    if profile is not None:
        files.append(("profile.json", _json_bytes(profile.export_fields(health))))
    setup = data.setup
    if setup is not None:
        files.append(("setup.json", json.dumps(setup.to_json(), ensure_ascii=False).encode("utf-8")))
    for path in data.calibration_files:
        try:
            with open(path, "rb") as f:
                files.append((f"calibrations/{os.path.basename(path)}", f.read()))
        except OSError:
            pass
    try:
        with open(data.events_path, "rb") as f:
            events = f.read()
        files.append(("events.jsonl", sanitize_events(events)))
    except OSError:
        pass
    return files


def sanitize_events(data, home=None):
    """事件里的文件路径带着用户名，导出前换成 ~。
    同一个路径在 JSON 里可能有几种写法：原样、JSON 转义过的（Windows 的 \\ 变成 \\\\）、Swift 写的 \\/、/ 和 \\ 互换的。"""
    home = home if home is not None else os.path.expanduser("~")
    text = data.decode("utf-8", errors="replace")
    variants = {
        json.dumps(home, ensure_ascii=False)[1:-1],      # JSON 转义（Windows：C:\\\\Users\\\\名字）
        home.replace("/", "\\/"),                         # Swift 的 JSONEncoder 把 / 写成 \/
        home.replace("\\", "/"),                          # Qt 等地方用 / 写 Windows 路径
        json.dumps(home.replace("\\", "/"), ensure_ascii=False)[1:-1],
        home,
    }
    for v in sorted((v for v in variants if len(v) > 1), key=len, reverse=True):
        text = text.replace(v, "~")
    return text.encode("utf-8")


def entries(zip_path):
    """列出 zip 里的条目。读不了返回 []。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return []


def append(files, zip_path, now=None):
    """追加到 zip。返回追加进去的条目名（zip 里的完整路径）。失败抛 ExportFailure。"""
    top = top_folder(entries(zip_path), os.path.basename(zip_path))
    names = []
    if not files:
        return names
    now = time.time() if now is None else now
    try:
        with zipfile.ZipFile(zip_path, "a") as zf:
            existing = set(zf.namelist())
            for rel, content in files:
                name = f"{top}/pet2/{rel}"
                if name in existing:
                    continue   # zip -q 遇到同名会替换；这里不会出现（每次导出都是新 zip），出现了就不重复加
                info = zipfile.ZipInfo(name, date_time=max(time.localtime(now)[:6], (1980, 1, 1, 0, 0, 0)))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, content)
                names.append(name)
    except (OSError, zipfile.BadZipFile) as e:
        raise ExportFailure(f"往 zip 里加 Pet2 的文件失败：{e}")
    return names

"""照翻 Pet2/Core/Pet2Data.swift：桌宠自己的数据文件。只用标准库。

文件夹里有 consent.json、profile.json、setup.json、onboarding.json、calibrations/<Unix秒>.json、export-log.jsonl；
events.jsonl 由 EventLog 写，也在这个文件夹。Mac 上权限 600；Windows 上用户文件夹本来就只有自己能读，chmod 只管只读标记。
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from . import paths
from .codable import DecodeError
from .consent import ConsentRecord
from .onboarding import OnboardingState
from .profile import Profile
from .setup_check import SetupInfo
from .swiftcompat import trim_whitespaces_and_newlines


@dataclass
class ExportLogEntry:
    time: float
    zip: str
    with_pain: bool
    with_points: bool
    participant_id: Optional[str] = None

    def to_json(self):
        out = {"time": self.time, "zip": self.zip, "with_pain": self.with_pain, "with_points": self.with_points}
        if self.participant_id is not None:
            out["participant_id"] = self.participant_id
        return out


def write_private(data, path):
    """写文件（先写临时文件再换过去），权限设成 600（只有自己能读写）。"""
    try:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        os.chmod(path, 0o600)
    except OSError:
        pass


class Pet2Data:
    def __init__(self, directory, backend_directory):
        self.directory = directory
        # 体态测试的数据文件夹（~/体态测试数据）。只读；“删除我的数据”时才删。
        self.backend_directory = backend_directory
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def default_directory():
        return paths.data_directory()

    @staticmethod
    def default_backend_directory():
        return paths.backend_directory()

    consent_path = property(lambda self: os.path.join(self.directory, "consent.json"))
    profile_path = property(lambda self: os.path.join(self.directory, "profile.json"))
    setup_path = property(lambda self: os.path.join(self.directory, "setup.json"))
    onboarding_path = property(lambda self: os.path.join(self.directory, "onboarding.json"))
    calibrations_path = property(lambda self: os.path.join(self.directory, "calibrations"))
    export_log_path = property(lambda self: os.path.join(self.directory, "export-log.jsonl"))
    events_path = property(lambda self: os.path.join(self.directory, "events.jsonl"))
    baseline_path = property(lambda self: os.path.join(self.backend_directory, "baseline.json"))
    log_path = property(lambda self: os.path.join(self.backend_directory, "log.csv"))

    # ---------- 读写 ----------

    @staticmethod
    def load(decoder, path):
        try:
            with open(path, "rb") as f:
                obj = json.loads(f.read().decode("utf-8"))
            return decoder(obj)
        except (OSError, ValueError, DecodeError, TypeError, KeyError):
            return None

    @staticmethod
    def save(value, path):
        data = json.dumps(value.to_json(), sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")
        write_private(data, path)

    @property
    def consent(self):
        r = self.load(ConsentRecord.from_json, self.consent_path)
        return r if r is not None else ConsentRecord()

    @property
    def profile(self):
        return self.load(Profile.from_json, self.profile_path)

    @property
    def setup(self):
        return self.load(SetupInfo.from_json, self.setup_path)

    @property
    def onboarding(self):
        r = self.load(OnboardingState.from_json, self.onboarding_path)
        return r if r is not None else OnboardingState()

    def save_consent(self, v):
        self.save(v, self.consent_path)

    def save_profile(self, v):
        self.save(v, self.profile_path)

    def save_setup(self, v):
        self.save(v, self.setup_path)

    def save_onboarding(self, v):
        self.save(v, self.onboarding_path)

    @property
    def participant_id(self):
        """匿名编号：后端第一次导出时写进 ~/体态测试数据/.participant_id。"""
        try:
            with open(os.path.join(self.backend_directory, ".participant_id"), "rb") as f:
                s = f.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        pid = trim_whitespaces_and_newlines(s)
        return pid if pid else None

    # ---------- 校准留档 ----------

    @property
    def baseline_time(self):
        """baseline.json 的 _time（后端每次校准成功会改它）。"""
        try:
            with open(self.baseline_path, "rb") as f:
                obj = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        v = obj.get("_time")
        if isinstance(v, (int, float)):   # NSNumber 里 true / false 也算数
            return float(v)
        return None

    def archive_baseline(self):
        """把当前 baseline.json 复制成 calibrations/<_time>.json。返回复制出的文件；没有 baseline 返回 None。"""
        try:
            with open(self.baseline_path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        t = self.baseline_time
        stamp = str(int(t)) if t is not None else str(int(time.time()))
        path = os.path.join(self.calibrations_path, f"{stamp}.json")
        write_private(data, path)
        return path

    @property
    def calibration_files(self):
        try:
            names = os.listdir(self.calibrations_path)
        except OSError:
            return []
        files = [n for n in names if os.path.splitext(n)[1] == ".json"]
        return [os.path.join(self.calibrations_path, n) for n in sorted(files)]

    # ---------- 导出记录 ----------

    def append_export_log(self, entry):
        line = (json.dumps(entry.to_json(), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with open(self.export_log_path, "rb") as f:
                data = f.read()
        except OSError:
            data = b""
        write_private(data + line, self.export_log_path)

    # ---------- 删除我的数据 ----------

    def deletion_targets(self):
        """要删的文件：体态测试数据里的 log*.csv、selftest*.csv、baseline.json、自测结果.txt、运行记录.txt、导出/；
        桌宠文件夹里的 consent / profile / setup / onboarding / calibrations / export-log / events。
        匿名编号 .participant_id 不删（gui 要用它对上已发过的数据）。"""
        out = []
        try:
            backend = os.listdir(self.backend_directory)
        except OSError:
            backend = []
        for name in sorted(backend):
            lower = name.lower()
            csv = lower.endswith(".csv")
            if (csv and lower.startswith("log")) or (csv and lower.startswith("selftest")) \
                    or name in ("baseline.json", "自测结果.txt", "运行记录.txt", "导出"):
                out.append(os.path.join(self.backend_directory, name))
        for path in (self.consent_path, self.profile_path, self.setup_path, self.onboarding_path,
                     self.calibrations_path, self.export_log_path, self.events_path):
            if os.path.exists(path):
                out.append(path)
        return out

    def delete_all(self):
        """真的删。返回删不掉的文件。"""
        failed = []
        for path in self.deletion_targets():
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    if sys.version_info >= (3, 12):
                        shutil.rmtree(path, onexc=_clear_readonly_and_retry)
                    else:
                        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
                else:
                    _remove_file(path)
            except OSError:
                failed.append(path)
        return failed


def _remove_file(path):
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)   # Windows 上只读的文件删不掉，先去掉只读
        os.remove(path)


def _clear_readonly_and_retry(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)

"""照翻 Pet2/Tests/OnboardingTests.swift：同意规则、资料导出处理、方向检查、zip 追加路径、删除清单。"""
import datetime
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile

from pet2win.core import export_package as ExportPackage
from pet2win.core.consent import Consent, ConsentChoice, ConsentRecord
from pet2win.core.onboarding import OnboardingState, Step
from pet2win.core.pet2_data import ExportLogEntry, Pet2Data
from pet2win.core.posture_input import Issue
from pet2win.core.profile import Profile
from pet2win.core.setup_check import DirectionCheck, Mirror, Result, SetupInfo

from .support import check

T0 = 1_793_000_000.0


def consent_rules():
    t0 = T0
    all_ = ConsentChoice(time=t0, adult=True, c1=True, c2=True, c3=True, c4=True, c5=True, c6=True)
    minor = Consent.normalized(ConsentChoice(time=t0, adult=False, c1=True, c2=True, c3=True, c4=True, c5=True), "US")
    check(minor.c1 and not minor.c2 and not minor.c3 and not minor.c4 and not minor.c5 and not minor.c6, "未满 18 只能勾 ①")
    check(not Consent.can_export(minor), "未满 18 不能导出")
    us = Consent.normalized(all_, "US")
    check(us.c2 and us.c3 and us.c4 and us.c5 and not us.c6, "⑥ 第一阶段不开放")
    cn = Consent.normalized(all_, "CN")
    check(not cn.c6 and not Consent.cross_border_available("CN"), "地区中国：⑥ 灰掉")
    no_research = Consent.normalized(ConsentChoice(time=t0, adult=True, c1=True, c2=False, c3=True, c4=True), "US")
    check(not Consent.can_export(no_research), "② 没勾不让导出")
    check(Consent.export_arguments(no_research, True, True) is None, "② 没勾没有导出参数")
    check(not no_research.c3 and not no_research.c4, "② 没勾时 ③④ 也不算")
    check(not Consent.can_use(None) and not Consent.can_use(ConsentChoice(time=t0, adult=True, c1=False)), "① 必选")

    only_research = ConsentChoice(time=t0, adult=True, c1=True, c2=True)
    check(Consent.export_arguments(only_research, True, True) == ["--export"],
          "只勾 ②：就算想带也不加 --with-pain / --with-points")
    check(not Consent.asks_pain(only_research) and not Consent.asks_points(only_research), "只勾 ②：不问疼痛分和坐标")
    pain_only = only_research.copy(); pain_only.c3 = True
    check(Consent.asks_pain(pain_only) and not Consent.asks_points(pain_only), "③ 勾了才问疼痛分")
    check(Consent.export_arguments(pain_only, True, True) == ["--export", "--with-pain"], "③ → --with-pain")
    check(Consent.export_arguments(pain_only, False, False) == ["--export"], "导出时还能选不带")
    check(Consent.export_arguments(us, True, True) == ["--export", "--with-pain", "--with-points"], "③④ 都勾、都想带")

    record = ConsentRecord()
    check(not Consent.record(us, "US", record), "第一次勾选不算撤回")
    off = us.copy(); off.c2 = False; off.time = t0 + 60
    check(Consent.record(off, "US", record), "撤回 ② 要提示")
    check(len(record.history) == 2 and record.current == record.history[-1] and record.current.c2 is False
          and record.version == "2", "history 追加，current 是最新一条")
    obj = json.loads(json.dumps(record.to_json()))
    cur = obj.get("current")
    check(obj.get("version") == "2" and cur.get("lang") == "zh" and cur.get("c1") is True
          and cur.get("adult") is True and len(obj.get("history")) == 2, "consent.json 的字段名")


def profile_export():
    check(Profile.age_band(17) == "na" and Profile.age_band(18) == "18-24" and Profile.age_band(24) == "18-24"
          and Profile.age_band(25) == "25-34" and Profile.age_band(44) == "35-44" and Profile.age_band(54) == "45-54"
          and Profile.age_band(64) == "55-64" and Profile.age_band(65) == "65+" and Profile.age_band(None) == "na", "年龄分档")
    check(Profile.round_to(172, 5) == 170 and Profile.round_to(173, 5) == 175 and Profile.round_to(67.4, 5) == 65, "取整到 5")
    p = Profile()
    p.age = 29; p.sex = "female"; p.height_cm = 163; p.weight_kg = 52.6; p.neck_history = "yes"
    p.country = "US"; p.us_state = "CA"; p.occupation = "student"
    with_health = p.export_fields(True)
    check(with_health.get("age_band") == "25-34" and "age" not in with_health, "导出只写 age_band，不写年龄数字")
    check(with_health.get("height_cm") == 165 and isinstance(with_health.get("height_cm"), int)
          and with_health.get("weight_kg") == 55 and with_health.get("neck_history") == "yes", "③ 勾了：身高体重取整、带病史")
    no_health = p.export_fields(False)
    check("height_cm" not in no_health and "weight_kg" not in no_health and "neck_history" not in no_health,
          "③ 没勾：去掉身高、体重、病史")
    check(no_health.get("us_state") == "CA" and no_health.get("sex") == "female", "其余照带")
    cn = Profile(**{**p.__dict__}); cn.country = "CN"
    check(cn.export_fields(False).get("us_state") == "na", "不在美国就不带州")
    blank = Profile(); blank.age = None
    check(blank.export_fields(True).get("height_cm") == "na", "不想说：na")


def direction_check():
    t0 = T0
    header = "time,label,issues,severity,tilt_to_left_deg,l_sh_x,l_sh_y,r_sh_x,r_sh_y,persons"

    def csv(rows):
        return "\n".join([header] + [f"{a},normal,,0,{b},1200,{c},1000,{d},1" for a, b, c, d in rows]) + "\n"

    left = csv([(t0, "8.0", "700", "710"), (t0 + 1, "9.5", "700", "710"), (t0 + 2, "7.1", "701", "709"),
                (t0 + 3, "-1", "700", "700")])
    r = DirectionCheck.evaluate(left, t0)
    check(r.mirror == Mirror.NOT_MIRRORED and r.shoulders_visible is True and r.rows == 4, "往左歪读到正数：没镜像")
    mirrored = csv([(t0, "-6", "700", "700"), (t0 + 1, "-7", "700", "700"), (t0 + 2, "-5", "700", "700")])
    check(DirectionCheck.evaluate(mirrored, t0).mirror == Mirror.MIRRORED, "读到负数：镜像")
    flat = csv([(t0, "1", "700", "700"), (t0 + 1, "-2", "700", "700"), (t0 + 2, "2.5", "700", "700")])
    check(DirectionCheck.evaluate(flat, t0).mirror == Mirror.UNCLEAR, "±3 以内：说不准")
    old = csv([(t0 - 30, "9", "700", "700"), (t0 + 1, "-8", "700", "700")])
    check(DirectionCheck.evaluate(old, t0).mirror == Mirror.MIRRORED, "只看 since 之后的行")
    low = csv([(t0, "8", "1060", "1070"), (t0 + 1, "8", "", ""), (t0 + 2, "8", "700", "")])
    r = DirectionCheck.evaluate(low, t0)
    check(r.shoulders_visible is False, "肩膀贴着画面底边 / 空：看不全")
    check(DirectionCheck.shoulders_visible(1040, 1040, None)
          and not DirectionCheck.shoulders_visible(1048, 1000, None)
          and DirectionCheck.shoulders_visible(1048, 1000, 1440), "97% 线，画面高度未知用 1080")
    check(DirectionCheck.evaluate(header + "\n", t0) == Result(Mirror.UNCLEAR, None, 0), "没有行")
    # 列换了位置也按列名读
    shuffled = f"persons,tilt_to_left_deg,time,r_sh_y,l_sh_y\n1,-9,{t0},700,700\n1,-9,{t0 + 1},700,700\n"
    check(DirectionCheck.evaluate(shuffled, t0).mirror == Mirror.MIRRORED, "按表头列名读")
    live = f"time,issues,severity,persons\n{t0 - 5},head_down,1,1\n{t0},head_down|shrug,1,1\n{t0 + 1},,0,1\n{t0 + 2},,,2\n"
    live_rows = DirectionCheck.rows(live, t0)
    check(len(live_rows) == 3 and live_rows[0].issues == [Issue.HEAD_DOWN, Issue.SHRUG]
          and sum(1 for x in live_rows if x.judged) == 2, "认识桌宠：取最近的行，两个人的行不算判断过")

    d = os.path.join(tempfile.gettempdir(), f"pet2-dir-{uuid.uuid4()}")
    os.makedirs(d)
    try:
        path = os.path.join(d, "log.csv")
        parts = [header + "\n"]
        for k in range(20000):
            parts.append(f"{t0 + float(k)},normal,,0,{'-8' if k >= 19990 else '8'},1200,700,1000,700,1\n")
        with open(path, "wb") as f:
            f.write("".join(parts).encode("utf-8"))
        tail = DirectionCheck.read_tail(path, 4096)
        check(tail is not None and tail.startswith(header + "\n")
              and DirectionCheck.evaluate(tail, t0 + 19990).mirror == Mirror.MIRRORED, "大文件只读表头和末尾")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def onboarding_state():
    t0 = T0
    s = OnboardingState()
    check(s.step == Step.WELCOME and not s.done and len(list(Step)) == 9, "9 步，从欢迎开始")
    check(Step.MEET_PET.skippable and Step.SELFTEST.skippable and not Step.CALIBRATE.skippable, "第 7、8 步可跳过")
    s.done = True; s.selftest_deferred_at = t0
    check(not s.should_remind_selftest(t0 + 3600) and s.should_remind_selftest(t0 + 24 * 3600), "24 小时后问一次")
    s.selftest_reminded = True
    check(not s.should_remind_selftest(t0 + 48 * 3600), "只问一次")


def data_folder():
    t0 = T0
    root = os.path.join(tempfile.gettempdir(), f"pet2-data-{uuid.uuid4()}")
    backend = os.path.join(root, "体态测试数据")
    mine = os.path.join(root, "BeibeijiaPet2")
    os.makedirs(os.path.join(backend, "导出"))
    home = os.path.expanduser("~")
    try:
        data = Pet2Data(mine, backend)
        check(data.participant_id is None, "还没导出过：没有编号")
        for name in ["log.csv", "log-alerts.csv", "log-old-1026-160924.csv", "log-alerts-old-1026-160924.csv", "selftest.csv",
                     "自测结果.txt", "运行记录.txt", ".running.pid", "别的.txt"]:
            with open(os.path.join(backend, name), "wb") as f:
                f.write(b"x\n")
        with open(os.path.join(backend, ".participant_id"), "wb") as f:
            f.write(b"df31f519\n")
        with open(data.baseline_path, "wb") as f:
            f.write(b'{"_time": 1793078406, "neck_ratio": 0.5}')
        check(data.participant_id == "df31f519" and data.baseline_time == 1_793_078_406, "读编号和 baseline 时间")

        record = ConsentRecord()
        Consent.record(ConsentChoice(time=t0, adult=True, c1=True, c2=True), "US", record)
        data.save_consent(record)
        profile = Profile(); profile.age = 30; profile.height_cm = 181; profile.neck_history = "yes"; profile.country = "US"
        data.save_profile(profile)
        data.save_setup(SetupInfo(camera="builtin", placement="desk", mirrored=False, shoulders_visible=True, checked_at=t0))
        archived = data.archive_baseline()
        check(archived is not None and os.path.basename(archived) == "1793078406.json" and len(data.calibration_files) == 1,
              "校准留档按 _time 命名")
        if sys.platform != "win32":   # Windows 没有 600 这种权限，用户文件夹本来就只有自己能读
            mode = stat.S_IMODE(os.stat(data.consent_path).st_mode)
            check(mode == 0o600, "文件权限 600")
        check(data.consent == record and data.profile == profile and data.setup is not None and data.setup.mirrored is False,
              "存了能读回来")
        with open(data.events_path, "wb") as f:
            f.write(('{"details":{"log":"' + home + '/体态测试数据/log.csv"},"kind":"app_started"}\n').encode("utf-8"))
        data.append_export_log(ExportLogEntry(time=t0, zip="a.zip", with_pain=False, with_points=False, participant_id="df31f519"))
        data.append_export_log(ExportLogEntry(time=t0 + 1, zip="b.zip", with_pain=True, with_points=False, participant_id="df31f519"))
        with open(data.export_log_path, "rb") as f:
            log_lines = [x for x in f.read().decode("utf-8").split("\n") if x]
        check(len(log_lines) == 2 and '"with_pain":true' in log_lines[1], "export-log.jsonl 一行一次")

        check(ExportPackage.top_folder(["p-1/log.csv", "p-1/manifest.json"], "p-1.zip") == "p-1", "顶层文件夹")
        check(ExportPackage.top_folder(["x/a", "y/b"], "p-2.zip") == "p-2", "不止一个顶层：用 zip 名")
        check(ExportPackage.top_folder([], "p-3.zip") == "p-3", "读不到列表：用 zip 名")

        # 做一个和后端一样结构的 zip（zip -r 会带上文件夹那一条），再追加。
        top_name = "posture-data-df31f519-20261027-120000"
        zip_path = os.path.join(backend, "导出", f"{top_name}.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(f"{top_name}/", b"")
            zf.writestr(f"{top_name}/manifest.json", b"{}")
        files = ExportPackage.pet2_files(data, t0, tz=datetime.timezone(datetime.timedelta(hours=-7)))   # 那天洛杉矶是 -0700
        rels = [rel for rel, _ in files]
        check(rels == ["manifest.json", "consent.json", "profile.json", "setup.json", "calibrations/1793078406.json",
                       "events.jsonl"], f"pet2/ 里的文件：{rels}")
        exported_profile = json.loads(files[2][1])
        check(exported_profile.get("age_band") == "25-34" and "height_cm" not in exported_profile
              and "neck_history" not in exported_profile and "age" not in exported_profile, "③ 没勾：导出的资料不带身高和病史")
        exported_consent = json.loads(files[1][1])
        check(exported_consent.get("time") == t0 and exported_consent.get("version") == "2", "导出的 consent.json 顶层有 time 和 version")
        manifest = json.loads(files[0][1])
        check(manifest.get("exported_at") == "2026-10-26T00:33:20-0700", f"manifest 时间格式和后端一样：{manifest.get('exported_at')}")
        events = files[5][1].decode("utf-8")
        check(home not in events and "~/体态测试数据" in events, "事件里的用户文件夹换成 ~")
        try:
            added = ExportPackage.append(files, zip_path)
        except ExportPackage.ExportFailure:
            added = None
        listed = ExportPackage.entries(zip_path)
        check(added is not None and len(added) == 6 and f"{top_name}/manifest.json" in listed
              and f"{top_name}/pet2/profile.json" in listed and f"{top_name}/pet2/calibrations/1793078406.json" in listed
              and not any(e.startswith("pet2/") for e in listed), f"pet2/ 在 zip 顶层文件夹下：{listed}")

        targets = {os.path.basename(p) for p in data.deletion_targets()}
        check(targets == {"log.csv", "log-alerts.csv", "log-old-1026-160924.csv", "log-alerts-old-1026-160924.csv", "selftest.csv",
                          "自测结果.txt", "运行记录.txt", "baseline.json", "导出",
                          "consent.json", "profile.json", "setup.json", "calibrations", "export-log.jsonl", "events.jsonl"},
              f"删除清单：{sorted(targets)}")
        check(data.delete_all() == [], "临时文件夹里删得掉")
        check(os.path.exists(os.path.join(backend, ".participant_id"))
              and os.path.exists(os.path.join(backend, "别的.txt"))
              and not os.path.exists(data.baseline_path) and data.onboarding == OnboardingState(), "只删清单里的；引导重置")
    finally:
        shutil.rmtree(root, ignore_errors=True)


ALL = [consent_rules, profile_export, direction_check, onboarding_state, data_folder]

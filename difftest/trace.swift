import Foundation

// 对拍用：拿 Mac 版的 Pet2/Core（只读，编进来，不改它）跑一段剧情或一批输入，把结果写成 JSON。
// Python 版（difftest/trace_py.py）跑同样的输入、写同样格式的 JSON，两边逐项比。
// 用法：trace scenario <剧情.json>   或   trace pure <输入.json>
// 所有小数都按二进制位（16 进制）进出，比较时一位都不差。
setbuf(stdout, nil)

func hexD(_ v: Any?) -> Double? {
    guard let s = v as? String else { return nil }
    return Double(bitPattern: UInt64(s, radix: 16)!)
}
func reqD(_ v: Any?) -> Double { hexD(v)! }
func enc(_ x: Double) -> String { String(x.bitPattern, radix: 16) }
func encOpt(_ x: Double?) -> Any { x.map(enc) ?? NSNull() }
func nul(_ x: Any?) -> Any { x ?? NSNull() }

func makeSettings(_ spec: [String: Any]) -> Brain.Settings {
    var s: Brain.Settings = (spec["base"] as? String) == "demo" ? .demo : Brain.Settings()
    if let aw = spec["allWindows"] as? [Any] { s.setAllWindows(reqD(aw[0]), upright: hexD(aw[1])) }
    if let set = spec["set"] as? [String: Any] {
        for (k, v) in set {
            switch k {
            case "windowCoverage": s.windowCoverage = reqD(v)
            case "coverageGapCap": s.coverageGapCap = reqD(v)
            case "slouchRemindEyeDrop": s.slouchRemindEyeDrop = reqD(v)
            case "rearmShare": s.rearmShare = reqD(v)
            case "uprightBubbleSeconds": s.uprightBubbleSeconds = reqD(v)
            case "sittingMinutes": s.sittingMinutes = reqD(v)
            case "awayAfter": s.awayAfter = reqD(v)
            case "leftSeatAfter": s.leftSeatAfter = reqD(v)
            case "sittingResetAfter": s.sittingResetAfter = reqD(v)
            case "correctedAfter": s.correctedAfter = reqD(v)
            case "postureFollow": s.postureFollow = reqD(v)
            case "sittingFollow": s.sittingFollow = reqD(v)
            case "snoozeSeconds": s.snoozeSeconds = reqD(v)
            case "maxRemindersPerHour": s.maxRemindersPerHour = v as! Int
            case "sameIssueGap": s.sameIssueGap = reqD(v)
            case "minGapBetweenReminders": s.minGapBetweenReminders = reqD(v)
            case "quietAfterReminder": s.quietAfterReminder = reqD(v)
            case "rewardSeconds": s.rewardSeconds = reqD(v)
            default: fatalError("不认识的设置 \(k)")
            }
        }
    }
    if let rules = spec["rules"] as? [String: Any] {
        for (k, v) in rules {
            let a = v as! [Any]
            s.rules[Issue(rawValue: k)!] = Brain.Settings.Rule(window: reqD(a[0]), share: reqD(a[1]), sameSide: a[2] as! Bool)
        }
    }
    if let removed = spec["removeRules"] as? [String] {
        for k in removed { s.rules[Issue(rawValue: k)!] = nil }
    }
    if let u = spec["upright"] as? [Any] { s.uprightRule = Brain.Settings.Rule(window: reqD(u[0]), share: reqD(u[1])) }
    if let r = spec["rewards"] as? [String: Any] {
        if let f = r["fast"] { s.rewards.fastFixSeconds = reqD(f) }
        if let h = r["heartEvery"] as? Int { s.rewards.heartEvery = h }
        if let secs = r["seconds"] as? [String: Any] {
            s.rewards.seconds = [:]
            for (k, v) in secs { s.rewards.seconds[RewardKind(rawValue: k)!] = reqD(v) }
        }
    }
    return s
}

func encodeScene(_ scene: Brain.Scene) -> [Any] {
    switch scene {
    case .waiting(let text): return ["waiting", text]
    case .away: return ["away"]
    case .calm: return ["calm"]
    case .drifting(let issue, let side, let i): return ["drifting", issue.rawValue, nul(side?.rawValue), enc(i)]
    case .reminding(let r):
        return ["reminding", r.id, r.kind.rawValue, nul(r.issue?.rawValue), nul(r.side?.rawValue), r.line, enc(r.startedAt), r.bubbleHidden]
    case .rewarding(let r):
        return ["rewarding", r.id, nul(r.issue?.rawValue), r.line, enc(r.startedAt), r.kind.rawValue, r.today, enc(r.seconds)]
    case .snoozed(let until): return ["snoozed", enc(until)]
    case .paused: return ["paused"]
    }
}

func probe(_ brain: Brain, _ t: TimeInterval) -> [Any] {
    var out: [Any] = ["probe", enc(t)]
    for issue in Issue.allCases {
        let side = brain.sameSideShare(of: issue, at: t)
        out.append([issue.rawValue, encOpt(brain.share(of: issue, at: t)),
                    encOpt(brain.share(of: issue, at: t, requireFullWindow: false)),
                    enc(brain.reminderProgress(of: issue, at: t)),
                    side.map { [$0.0.rawValue, enc($0.1)] as [Any] } ?? NSNull()])
    }
    out.append(["upright", encOpt(brain.uprightShare(at: t)), encOpt(brain.uprightShare(at: t, requireFullWindow: false))])
    out.append(["state", brain.paused, encOpt(brain.snoozeUntil), encOpt(brain.latestRow?.t), encOpt(brain.lastRowReceivedAt),
                encOpt(brain.lastCrowdedAt), brain.reminderSeesCorrection])
    return out
}

func runScenario(_ spec: [String: Any]) -> [Any] {
    let brain = Brain(settings: makeSettings(spec["settings"] as! [String: Any]))
    if let status = spec["inputStatus"] as? String { brain.inputStatus = status }
    var trace: [Any] = []
    brain.onEvent = { kind, details, t in trace.append(["event", kind, details, enc(t)]) }
    for case let op as [Any] in spec["ops"] as! [Any] {
        let name = op[0] as! String
        switch name {
        case "row":
            let r = op[3] as! [String: Any]
            var row = PostureRow(t: reqD(op[1]), issues: (r["issues"] as! [String]).map { Issue(rawValue: $0)! },
                                 severity: reqD(r["severity"]), tiltToLeft: hexD(r["tilt"]), turnToLeft: hexD(r["turn"]),
                                 leftShoulderUp: hexD(r["shoulder"]), persons: r["persons"] as? Int, judged: r["judged"] as! Bool)
            row.eyeDrop = hexD(r["eyeDrop"])
            brain.ingest(row, now: reqD(op[2]))
        case "alert":
            let a = op[2] as! [String: Any]
            let alert = AlertRow(alertTime: reqD(a["time"]), kind: AlertRow.Kind(rawValue: a["kind"] as! String)!,
                                 issue: (a["issue"] as? String).flatMap(Issue.init(rawValue:)),
                                 outcome: AlertRow.Outcome(rawValue: a["outcome"] as! String)!, outcomeTime: reqD(a["outcomeTime"]))
            brain.ingest(alert, now: reqD(op[1]))
        case "tick": brain.tick(now: reqD(op[1]))
        case "snooze": brain.snooze(now: reqD(op[1]))
        case "cancelSnooze": brain.cancelSnooze(now: reqD(op[1]))
        case "ack": brain.acknowledge(now: reqD(op[1]))
        case "pause": brain.setPaused(op[2] as! Bool, now: reqD(op[1]))
        case "presented": brain.rewardPresented(id: op[1] as! Int, played: op[2] as! Bool, now: reqD(op[3]))
        case "scene": trace.append(["scene", enc(reqD(op[1])), encodeScene(brain.scene(now: reqD(op[1])))])
        case "probe": trace.append(probe(brain, reqD(op[1])))
        default: fatalError("不认识的操作 \(name)")
        }
    }
    return trace
}

// MARK: - 纯函数

func choice(_ d: [String: Any]) -> ConsentChoice {
    ConsentChoice(time: reqD(d["time"]), adult: d["adult"] as! Bool, c1: d["c1"] as! Bool, c2: d["c2"] as! Bool,
                  c3: d["c3"] as! Bool, c4: d["c4"] as! Bool, c5: d["c5"] as! Bool, c6: d["c6"] as! Bool)
}
func encChoice(_ c: ConsentChoice) -> [Any] { [enc(c.time), c.version, c.lang, c.adult, c.c1, c.c2, c.c3, c.c4, c.c5, c.c6] }

func encRow(_ r: PostureRow?) -> Any {
    guard let r else { return NSNull() }
    return [enc(r.t), r.issues.map(\.rawValue), enc(r.severity), encOpt(r.tiltToLeft), encOpt(r.turnToLeft),
            encOpt(r.leftShoulderUp), nul(r.persons), encOpt(r.eyeDrop), r.judged,
            nul(r.side(of: .headTilt)?.rawValue), nul(r.side(of: .headTurn)?.rawValue), nul(r.side(of: .unevenShoulders)?.rawValue)]
}

func runPure(_ input: [String: Any]) -> [String: Any] {
    var out: [String: Any] = [:]
    out["doubles"] = (input["doubles"] as! [String]).map { encOpt(Double($0)) }
    out["ints"] = (input["ints"] as! [String]).map { nul(Int($0)) }
    out["int32s"] = (input["ints"] as! [String]).map { nul(Int32($0).map { Int($0) }) }
    out["trims"] = (input["trims"] as! [String]).map { [$0.trimmingCharacters(in: .whitespaces), $0.trimmingCharacters(in: .whitespacesAndNewlines)] }
    out["splitNewlines"] = (input["splitNewlines"] as! [String]).map { $0.split(whereSeparator: \.isNewline).map(String.init) }
    out["csvSplit"] = (input["csvSplit"] as! [String]).map { CSVTail.split($0) }
    out["rows"] = (input["rows"] as! [[String: String]]).map { encRow(PostureRow(csv: $0)) }
    out["alerts"] = (input["alerts"] as! [[String: String]]).map { d -> Any in
        guard let a = AlertRow(csv: d) else { return NSNull() }
        return [enc(a.alertTime), a.kind.rawValue, nul(a.issue?.rawValue), a.outcome.rawValue, enc(a.outcomeTime)]
    }
    out["evaluate"] = (input["evaluate"] as! [[Any]]).map { a -> Any in
        let r = DirectionCheck.evaluate(csv: a[0] as! String, since: reqD(a[1]), until: hexD(a[2]) ?? .infinity, frameHeight: hexD(a[3]))
        return ["\(r.mirror)", nul(r.shouldersVisible), r.rows]
    }
    out["rowsText"] = (input["rowsText"] as! [[Any]]).map { a -> Any in
        DirectionCheck.rows(csv: a[0] as! String, since: reqD(a[1])).map { encRow($0) }
    }
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("pet2-difftest-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: tmp) }
    out["readTail"] = (input["readTail"] as! [[Any]]).map { a -> Any in
        let file = tmp.appendingPathComponent("log.csv")
        try! Data(base64Encoded: a[0] as! String)!.write(to: file)
        return nul(DirectionCheck.readTail(path: file.path, bytes: a[1] as! Int))
    }
    out["lines"] = (input["lines"] as! [[Any]]).map { a -> Any in
        Lines.reminder(Issue(rawValue: a[0] as! String)!, side: (a[1] as? String).flatMap(Side.init(rawValue:)), window: reqD(a[2]))
    }
    out["sittingLines"] = (input["sittingMinutes"] as! [String]).map { Lines.sitting(minutes: Int(reqD($0).rounded())) }
    out["ageBands"] = (input["ageBands"] as! [Any]).map { Profile.ageBand($0 as? Int) }
    out["roundTo"] = (input["roundTo"] as! [[String]]).map { Profile.roundTo(reqD($0[0]), step: reqD($0[1])) }
    out["consent"] = (input["consent"] as! [[Any]]).map { a -> Any in
        let c = choice(a[0] as! [String: Any])
        let country = a[1] as? String
        let n = Consent.normalized(c, country: country)
        var record = ConsentRecord()
        let first = Consent.record(c, country: country, into: &record)
        var flipped = c; flipped.c2 = !c.c2
        let second = Consent.record(flipped, country: country, into: &record)
        return [encChoice(n), Consent.canUse(n), Consent.canExport(c), Consent.asksPain(c), Consent.asksPoints(c),
                nul(Consent.exportArguments(c, wantPain: true, wantPoints: true)),
                nul(Consent.exportArguments(c, wantPain: false, wantPoints: true)),
                nul(Consent.exportArguments(n, wantPain: true, wantPoints: false)), first, second,
                record.history.map(encChoice)]
    }
    out["pick"] = (input["pick"] as! [[Any]]).map { a -> Any in
        var rules = RewardRules()
        rules.heartEvery = a[3] as! Int
        rules.fastFixSeconds = reqD(a[4])
        let issue = (a[0] as? String).flatMap(Issue.init(rawValue:))
        let kind = rules.pick(issue: issue, secondsSinceReminder: hexD(a[1]), today: a[2] as! Int)
        return [kind.rawValue, RewardRules.line(kind, issue: issue, today: a[2] as! Int)]
    }
    out["topFolder"] = (input["topFolder"] as! [[Any]]).map { a -> Any in
        ExportPackage.topFolder(entries: a[0] as! [String], zipName: a[1] as! String)
    }
    out["drowsiness"] = (input["drowsiness"] as! [String]).map { "\(Drowsiness.stage(progress: reqD($0)))" }
    out["median"] = (input["median"] as! [[String]]).map { encOpt(DirectionCheck.median($0.map(reqD))) }
    out["mirror"] = (input["median"] as! [[String]]).map { "\(DirectionCheck.mirror(fromTilts: $0.map(reqD)))" }
    out["pidRunning"] = (input["pidRunning"] as! [Any]).map { v -> Any in
        PostureExit.someoneElseRunning(pidFileContents: v as? String, isAlive: { $0 % 2 == 1 })
    }
    out["permission"] = (input["permission"] as! [String]).map { PostureExit.isPermissionProblem($0) }
    out["dayKey"] = (input["dayKey"] as! [String]).map { CorrectionCounter.dayKey(reqD($0)) }
    out["onboarding"] = (input["onboarding"] as! [[Any]]).map { a -> Any in
        var s = OnboardingState()
        s.done = a[0] as! Bool
        s.selftest_deferred_at = hexD(a[1])
        s.selftest_reminded = a[2] as! Bool
        return s.shouldRemindSelftest(now: reqD(a[3]))
    }
    out["shouldersVisible"] = (input["shouldersVisible"] as! [[Any]]).map { a -> Any in
        DirectionCheck.shouldersVisible(leftY: hexD(a[0]), rightY: hexD(a[1]), frameHeight: hexD(a[2]))
    }
    return out
}

// MARK: - 读文件（CSVTail 跟着一串文件操作走）

func runTail(_ spec: [String: Any]) -> [Any] {
    let dirURL = FileManager.default.temporaryDirectory.appendingPathComponent("pet2-difftail-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: dirURL, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dirURL) }
    let dir = dirURL.path
    let path = (dir as NSString).appendingPathComponent("log.csv")
    var tails: [CSVTail] = []
    var trace: [Any] = []
    for case let op as [Any] in spec["ops"] as! [Any] {
        switch op[0] as! String {
        case "open": tails.append(CSVTail(path: path))
        case "write": try! (op[1] as! String).data(using: .utf8)!.write(to: URL(fileURLWithPath: path))
        case "append":
            if let h = FileHandle(forWritingAtPath: path) {
                h.seekToEndOfFile(); h.write((op[1] as! String).data(using: .utf8)!); h.closeFile()
            } else {
                try! (op[1] as! String).data(using: .utf8)!.write(to: URL(fileURLWithPath: path))
            }
        case "appendBytes":
            let bytes = Data((op[1] as! [Int]).map { UInt8($0) })
            if let h = FileHandle(forWritingAtPath: path) { h.seekToEndOfFile(); h.write(bytes); h.closeFile() }
        case "remove": try? FileManager.default.removeItem(atPath: path)
        case "rename": try? FileManager.default.moveItem(atPath: path, toPath: (dir as NSString).appendingPathComponent(op[1] as! String))
        case "poll":
            let tail = tails[op[1] as! Int]
            let rows = tail.poll()
            let status: Any
            switch tail.status {
            case .missing: status = "missing"
            case .unreadable: status = "unreadable"
            case .waitingHeader: status = "waiting_header"
            case .reading(let n): status = ["reading", n]
            }
            trace.append([rows, status, tail.columns])
        default: fatalError("不认识的操作")
        }
    }
    return trace
}

let args = CommandLine.arguments
let data = try! Data(contentsOf: URL(fileURLWithPath: args[2]))
let input = try! JSONSerialization.jsonObject(with: data)
let result: Any
switch args[1] {
case "scenario": result = (input as! [[String: Any]]).map { runScenario($0) }
case "pure": result = runPure(input as! [String: Any])
case "tail": result = (input as! [[String: Any]]).map { runTail($0) }
default: fatalError("用法：trace scenario|pure|tail <输入.json>")
}
let output = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
FileHandle.standardOutput.write(output)

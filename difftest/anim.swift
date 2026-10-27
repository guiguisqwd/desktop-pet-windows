import Foundation

// 对拍弹簧动作：拿 Mac 版的 Pet2/Mac/Animator.swift（只读编进来）按一串幕跑，每帧输出姿势的二进制位。
// 用法：anim <输入.json>
func hexD(_ v: Any?) -> Double { Double(bitPattern: UInt64(v as! String, radix: 16)!) }
func enc(_ x: CGFloat) -> String { String(Double(x).bitPattern, radix: 16) }

func act(_ a: [Any]) -> Act {
    let kind = a[0] as! String
    switch kind {
    case "waiting": return .waiting
    case "away": return .away
    case "calm": return .calm
    case "snoozed": return .snoozed
    case "paused": return .paused
    case "drifting": return .drifting(Issue(rawValue: a[1] as! String)!, (a[2] as? String).flatMap(Side.init(rawValue:)))
    case "reminding": return .reminding(id: a[1] as! Int)
    case "remindingUpright": return .remindingUpright(id: a[1] as! Int)
    case "noticed": return .noticed(id: a[1] as! Int)
    case "rewarding": return .rewarding(id: a[1] as! Int)
    default: fatalError(kind)
    }
}

let keys: [WritableKeyPath<RigPose, CGFloat>] = [\.rootX, \.rootY, \.bodyAngle, \.headAngle, \.chestAngle, \.chestLift,
    \.shoulderLift, \.leftArm, \.rightArm, \.leftForearm, \.rightForearm, \.tailAngle, \.bodyLift, \.leftFootLift,
    \.rightFootLift, \.blink, \.smile, \.mouthOpen, \.cheek, \.exhale, \.sign, \.signLift, \.opacity]

let input = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [[Any]]
var out: [Any] = []
for seq in input {
    let animator = Animator()
    var frames: [Any] = []
    for case let step as [Any] in seq {
        animator.step(act: act(step[0] as! [Any]), intensity: hexD(step[1]), now: hexD(step[2]), dt: hexD(step[3]))
        frames.append(keys.map { enc(animator.pose[keyPath: $0]) } + [enc(animator.squash), enc(animator.doze)])
    }
    out.append(frames)
}
FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: out))

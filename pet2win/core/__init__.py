"""照翻 Pet2/Core（Mac 版）：决定全在这里，只用 Python 标准库。和 Swift 版逐个文件对应：

  swiftcompat.py   Swift 和 Python 不一样的几处细节（Double()、rounded()、去空格、换行）
  posture_input.py PostureInput.swift   log.csv / log-alerts.csv 的一行
  csv_tail.py      CSVTail.swift        跟着读不断追加的 CSV
  brain.py         Brain.swift          状态机：演哪一幕、什么时候提醒、什么时候奖励；桌宠说的话（Lines）
  rewards.py       Rewards.swift        奖励放哪个 GIF
  backoff.py       Backoff.swift        后台起不来时隔多久再试
  drowsiness.py    Drowsiness.swift     犯困 / 睡着
  event_log.py     EventLog.swift       events.jsonl
  consent.py       Consent.swift        同意书第 2 版的勾选和规则
  profile.py       Profile.swift        基本资料
  setup_check.py   SetupCheck.swift     setup.json；方向检查
  onboarding.py    Onboarding.swift     引导走到哪一步
  pet2_data.py     Pet2Data.swift       桌宠自己的文件、校准留档、导出记录、删除清单
  export_package.py ExportPackage.swift 往后端的 zip 里追加 pet2/
另外两个是 Windows 才要的：paths.py（文件夹在哪）、fileio.py（读别人正在写的文件）。
"""

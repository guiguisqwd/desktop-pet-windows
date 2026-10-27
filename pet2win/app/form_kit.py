"""照翻 Pet2/Mac/FormKit.swift：引导窗口和“我的数据”窗口共用的小零件：文字、按钮、下拉框，以及同意书勾选那一页。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget)

from pet2win.core.consent import ConsentChoice

from . import resources
from .pet_canvas import ui_font

FORM_WIDTH = 470

SECONDARY = "#6e6e73"
RED = "#d1352b"
GREEN = "#248a3d"
ORANGE = "#c46a00"
TEAL = "#1f8a96"


def text(s, size=13, weight=QFont.Normal, color=None):
    label = QLabel(s)
    label.setWordWrap(True)
    label.setFont(ui_font(size, weight))
    label.setMaximumWidth(FORM_WIDTH)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    if color:
        set_color(label, color)
    return label


def note(s):
    return text(s, size=11.5, color=SECONDARY)


def set_color(label, color):
    label.setStyleSheet(f"color: {color};")


def _box(layout, views, spacing):
    layout.setSpacing(spacing)
    layout.setContentsMargins(0, 0, 0, 0)
    for v in views:
        if v is None:
            layout.addStretch(1)
        elif isinstance(v, QWidget):
            layout.addWidget(v)
        else:
            layout.addLayout(v)
    w = QWidget()
    w.setLayout(layout)
    return w


def row(views, spacing=8):
    """横排。views 里放 None 表示这里撑开（Mac 的 NSView() 占位）。"""
    layout = QHBoxLayout()
    layout.setAlignment(Qt.AlignVCenter)
    return _box(layout, views, spacing)


def column(views, spacing=10):
    layout = QVBoxLayout()
    layout.setAlignment(Qt.AlignTop)
    return _box(layout, views, spacing)


def push(title, handler=None, primary=False):
    b = QPushButton(title)
    b.setFont(ui_font(13))
    if primary:
        b.setDefault(True)
        b.setAutoDefault(True)
    else:
        b.setAutoDefault(False)
    if handler:
        b.clicked.connect(lambda _=False: handler())
    return b


def check(title, on=False, handler=None):
    b = QCheckBox(title)
    b.setFont(ui_font(13))
    b.setChecked(on)
    if handler:
        b.toggled.connect(lambda _=False: handler())
    return b


class Choice:
    """下拉框：显示中文，取值是代码。"""

    def __init__(self, pairs, selected):
        self.codes = [c for c, _ in pairs]
        self.popup = QComboBox()
        self.popup.setFont(ui_font(13))
        self.popup.addItems([t for _, t in pairs])
        if selected in self.codes:
            self.popup.setCurrentIndex(self.codes.index(selected))
        self.on_change = None
        self.popup.currentIndexChanged.connect(lambda _: self.on_change and self.on_change())

    @property
    def value(self):
        return self.codes[max(0, self.popup.currentIndex())]


def labeled(title, control):
    """一行：标签 + 控件。"""
    label = QLabel(title)
    label.setFont(ui_font(13))
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    label.setFixedWidth(110)
    if not isinstance(control, QWidget):
        control = _box(QHBoxLayout(), [control], 8)
    return row([label, control, None])


class Number:
    """数字输入框 + “不想说”。"""

    def __init__(self, value, unit, placeholder=""):
        self.field = QLineEdit()
        self.field.setFont(ui_font(13))
        self.field.setPlaceholderText(placeholder)
        self.field.setFixedWidth(70)
        if value is not None:
            self.field.setText(str(int(value)) if value == round(value) else str(value))
        unit_label = QLabel(unit)
        unit_label.setFont(ui_font(13))
        self.decline = check("不想说", handler=self._sync)
        self.view = row([self.field, unit_label, self.decline])

    def set_declined(self, on):
        self.decline.setChecked(on)
        self._sync()

    def _sync(self):
        self.field.setEnabled(not self.decline.isChecked())
        if self.decline.isChecked():
            self.field.setText("")

    @property
    def value(self):
        """None：不想说 / 没填 / 不是数字。"""
        if self.decline.isChecked():
            return None
        s = self.field.text().strip().replace("，", ".")
        try:
            v = float(s)
        except ValueError:
            return None
        return v if v == v and v not in (float("inf"), float("-inf")) else None

    @property
    def is_empty(self):
        return self.field.text().strip() == ""


def show_text(title, body, parent=None):
    """弹一个只能读的长文本窗口（同意书全文）。"""
    w = QDialog(parent)
    w.setWindowTitle(title)
    w.resize(560, 600)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setPlainText(body)
    view.setFont(ui_font(13))
    layout = QVBoxLayout(w)
    layout.addWidget(view)
    w.setModal(False)
    w.show()
    w.raise_()
    w.activateWindow()
    return w


CANCEL_WORDS = ("取消", "算了", "不带", "好", "不用了")


def alert(title, body, buttons=("好",), parent=None):
    """弹一个对话框，返回点了第几个按钮（从 0 数）。第一个按钮是默认的（回车），Esc 等于“取消 / 算了 / 不带”。
    按钮按给的顺序排（Mac 的 NSAlert 第一个按钮是默认）。"""
    box = QMessageBox(parent)
    box.setWindowTitle("赛博背背佳")
    box.setText(title)
    if body:
        box.setInformativeText(body)
    box.setIcon(QMessageBox.NoIcon)
    made = [box.addButton(label, QMessageBox.ActionRole) for label in buttons]
    box.setDefaultButton(made[0])
    cancel = next((i for i, label in enumerate(buttons) if label in CANCEL_WORDS), 0)
    box.setEscapeButton(made[cancel])
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.exec()
    clicked = box.clickedButton()
    return made.index(clicked) if clicked in made else cancel


class ConsentForm:
    """同意那一页：先问满不满 18；三句话版 + 看全文；勾 ①–⑥。引导第 2 步和“我的数据 → 改勾选”共用。"""
    labels = [
        "① 我已读懂并同意使用产品（必选）",
        "② 我年满 18 岁，同意我的去标识数据用于研究（包括摸底测试的角度）",
        "③ 我同意导出我的疼痛 / 拉扯打分、身高体重和颈肩病史用于研究",
        "④ 我同意导出 7 个关键点的原始坐标，用来改进算法",
        "⑤ 可以联系我，邀请我参加以后的研究",
        "⑥ 我同意数据传到中国境外（仅中国地区，第一阶段不开放）",
    ]

    def __init__(self, current):
        self.on_change = None
        self._adult_answer = None
        self._full_text_window = None
        self.adult_yes = QRadioButton("满 18 岁了")
        self.adult_no = QRadioButton("还没有")
        for b in (self.adult_yes, self.adult_no):
            b.setFont(ui_font(13))
        self.boxes = [QCheckBox(label) for label in self.labels]
        for b in self.boxes:
            b.setFont(ui_font(13))
            b.setStyleSheet("QCheckBox { spacing: 6px; }")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        summary = column([
            text("· 不录像、不拍照，也不做人脸识别。"),
            text("· 数据只在你的电脑上，没有自动上传。"),
            text("· 导出要你自己点，勾选随时可以撤回。"),
        ], spacing=4)
        full = push("看全文…", self._show_full_text)
        self.view = column([
            row([text("你满 18 岁了吗？", weight=QFont.DemiBold), self.adult_yes, self.adult_no, None], spacing=14),
            summary,
            row([full, None]),
            column(self.boxes, spacing=6),
            note("除了 ①，其他默认都不勾；不勾不影响使用。未满 18 岁只能勾 ①。"),
        ], spacing=12)
        if current is not None:
            self._adult_answer = current.adult
            self.adult_yes.setChecked(current.adult)
            self.adult_no.setChecked(not current.adult)
            for b, on in zip(self.boxes, [current.c1, current.c2, current.c3, current.c4, current.c5, current.c6]):
                b.setChecked(on)
        else:
            self.boxes[0].setChecked(True)
        self.adult_yes.clicked.connect(lambda: self._set_adult(True))
        self.adult_no.clicked.connect(lambda: self._set_adult(False))
        for b in self.boxes:
            b.clicked.connect(lambda _=False: self._sync())
        self._sync()

    def _set_adult(self, yes):
        self._adult_answer = yes
        self.adult_yes.setChecked(yes)
        self.adult_no.setChecked(not yes)
        self._sync()

    def _sync(self):
        """按规则灰掉：没回答年龄 / 未满 18 → 只能 ①；② 没勾 → ③④ 灰掉；⑥ 不开放。"""
        adult = self._adult_answer is True
        for i in range(1, 5):
            self.boxes[i].setEnabled(adult)
        if not adult:
            for i in range(1, 6):
                self.boxes[i].setChecked(False)
        research = adult and self.boxes[1].isChecked()
        for i in (2, 3):
            self.boxes[i].setEnabled(research)
            if not research:
                self.boxes[i].setChecked(False)
        self.boxes[5].setEnabled(False)
        self.boxes[5].setChecked(False)
        if self.on_change:
            self.on_change()

    @property
    def is_complete(self):
        return self._adult_answer is not None and self.boxes[0].isChecked()

    def choice(self, now):
        if self._adult_answer is None or not self.is_complete:
            return None
        b = [x.isChecked() for x in self.boxes]
        return ConsentChoice(time=now, adult=self._adult_answer, c1=b[0], c2=b[1], c3=b[2], c4=b[3], c5=b[4], c6=b[5])

    def _show_full_text(self):
        path = resources.consent_text_path()
        body = "（没找到同意书全文，请联系 gui）"
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    body = f.read()
            except OSError:
                pass
        self._full_text_window = show_text("使用与研究同意书（第 2 版）", body)

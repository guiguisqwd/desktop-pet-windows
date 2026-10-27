"""照翻 Pet2/Core/CSVTail.swift：跟着一个不断追加的 CSV 文件读新行（像 tail -f）。只用标准库。

- 表头按列名读，列多了、顺序变了都不怕。
- 第一次打开时跳过已有的旧行，只读之后新写的；文件被换掉（改名、重建）或被清空时，从头重新读。
- 文件还没表头（刚建、还是空的）就等着，下次再读。
"""
import os
from dataclasses import dataclass

from .fileio import open_shared
from .swiftcompat import trim_whitespaces


@dataclass(frozen=True)
class Status:
    """missing：文件不存在；unreadable：存在但打不开；waiting_header：还没有表头；reading：在读（rows 读了几行）。"""
    kind: str
    rows: int = 0


MISSING = Status("missing")
UNREADABLE = Status("unreadable")
WAITING_HEADER = Status("waiting_header")


def reading(rows):
    return Status("reading", rows)


class CSVTail:
    def __init__(self, path, header_first_column="time"):
        self.path = os.path.expanduser(path)
        self.header_first_column = header_first_column   # 只认以这一列开头的表头
        self.status = MISSING
        self.columns = {}
        self._offset = 0
        self._file_id = None
        self._remainder = ""
        self._ever_opened = False
        self._rows_read = 0

    def poll(self):
        """读出自上次以来的新行。每行是 列名 → 值。"""
        try:
            st = os.stat(self.path)
        except OSError:
            # 之后再出现的文件是新建的，里面的行都算新行。
            self._ever_opened = True
            self._reset()
            self.status = MISSING
            return []
        size = st.st_size
        file_id = st.st_ino
        skip_existing = False
        if self._file_id is None or file_id != self._file_id or size < self._offset:
            # 第一次看到这个文件：跳过旧行。之后文件换了：新文件里的都是新行。
            skip_existing = not self._ever_opened
            self._ever_opened = True
            self._reset()
            self._file_id = file_id
        if not (size > self._offset or not self.columns):
            self.status = WAITING_HEADER if not self.columns else reading(self._rows_read)
            return []
        try:
            handle = open_shared(self.path)
        except OSError:
            self.status = UNREADABLE
            return []
        with handle:
            try:
                handle.seek(self._offset)
            except OSError:
                self.status = UNREADABLE
                return []
            try:
                data = handle.read()
            except OSError:
                data = b""
        self._offset += len(data)

        try:
            chunk = data.decode("utf-8")
        except UnicodeDecodeError:
            chunk = ""   # 和 Swift 的 String(data:encoding:) ?? "" 一样：这一段解不出来就当空的
        text = self._remainder + chunk
        lines = text.split("\n")
        self._remainder = lines.pop()   # 最后一段可能还没写完
        rows = []
        for raw in lines:
            line = raw.strip("\r")
            if not line:
                continue
            fields = self.split(line)
            if fields and trim_whitespaces(fields[0]) == self.header_first_column:
                self.columns = {}
                for index, name in enumerate(fields):
                    self.columns[trim_whitespaces(name)] = index
                continue
            if not self.columns:
                continue
            if skip_existing:
                continue
            row = {}
            for name, index in self.columns.items():
                if index < len(fields):
                    row[name] = fields[index]
            rows.append(row)
        self._rows_read += len(rows)
        self.status = WAITING_HEADER if not self.columns else reading(self._rows_read)
        return rows

    def _reset(self):
        self._offset = 0
        self._file_id = None
        self._remainder = ""
        self.columns = {}

    @staticmethod
    def split(line):
        """简单 CSV：支持双引号包起来的字段（后端现在不会写逗号，但以防万一）。"""
        if '"' not in line:
            return line.split(",")
        fields = []
        current = []
        quoted = False
        chars = list(line)
        i = 0
        pending = None
        n = len(chars)
        while True:
            if pending is not None:
                ch, pending = pending, None
            elif i < n:
                ch = chars[i]
                i += 1
            else:
                break
            if quoted:
                if ch == '"':
                    if i < n:
                        nxt = chars[i]
                        i += 1
                        if nxt == '"':
                            current.append('"')
                        else:
                            quoted = False
                            pending = nxt
                    else:
                        quoted = False
                else:
                    current.append(ch)
            elif ch == '"':
                quoted = True
            elif ch == ",":
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        fields.append("".join(current))
        return fields

"""Swift 和 Python 在几处细节上不一样，这里按 Swift 的规矩写，让翻过来的 Core 和 Mac 版算出一模一样的结果。

- Double("…")：不认前后空格、不认下划线和全角数字；认十六进制（0x1.8p1）、inf、nan。
  Python 的 float() 这些都认（" 12" → 12.0），直接用会和 Mac 版不一样。
- sorted()：有 NaN 时排出来的顺序和 Python 的 sorted() 不一样（方向检查取中位数会用到）。
- Int("…")：只认 [+-]数字。
- .rounded()：四舍五入（.5 往远离 0 的方向）。Python 的 round() 是“四舍六入五成双”。
- trimmingCharacters(in: .whitespaces)：只去 Zs 类空格、Tab 和零宽空格，不去换行。Python 的 strip() 连换行一起去。
- Character.isNewline：\\n \\r \\v \\f U+0085 U+2028 U+2029，\\r\\n 算一个。str.splitlines() 还会在 \\x1c-\\x1e 断开。
只用标准库。
"""
import math
import re
import struct

# CharacterSet.whitespaces：Unicode Zs 类 + Tab（U+0009），苹果的实现还多一个零宽空格 U+200B（在 Mac 上逐个码位核对过）
WHITESPACES = "".join(chr(c) for c in [0x09, 0x20, 0xA0, 0x1680] + list(range(0x2000, 0x200C)) + [0x202F, 0x205F, 0x3000])
# CharacterSet.whitespacesAndNewlines：再加上 U+000A–U+000D、U+0085、U+2028、U+2029
WHITESPACES_AND_NEWLINES = WHITESPACES + "".join(chr(c) for c in [0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029])
NEWLINES = "".join(chr(c) for c in [0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029])

_NEWLINE_RUN = re.compile("[" + re.escape(NEWLINES) + "]+")

_DEC = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z", re.ASCII)
_HEX = re.compile(r"[+-]?0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?\Z", re.ASCII)
_INF = re.compile(r"[+-]?(?:inf|infinity)\Z", re.ASCII | re.IGNORECASE)
_NAN = re.compile(r"([+-]?)nan(?:\(([^)]*)\))?\Z", re.ASCII | re.IGNORECASE)
_SNAN = re.compile(r"[+-]?snan\Z", re.ASCII | re.IGNORECASE)
_INT = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)


def trim_whitespaces(s):
    """s.trimmingCharacters(in: .whitespaces)"""
    return s.strip(WHITESPACES)


def trim_whitespaces_and_newlines(s):
    """s.trimmingCharacters(in: .whitespacesAndNewlines)"""
    return s.strip(WHITESPACES_AND_NEWLINES)


def split_newlines(text):
    """text.split(whereSeparator: \\.isNewline)：按换行断开，空的段不要。"""
    return [part for part in _NEWLINE_RUN.split(text) if part]


def swift_double(s):
    """Double(s)：Swift 的规矩（在 Mac 上用 Swift 6.3 逐个核对过）。不认就返回 None。
    - 不认开头的空白、空串；整串都要用上。
    - 十进制、十六进制（0x1.8p1）都认；太大是 ±inf，太小是 ±0（不报错）。
    - inf / infinity / nan / nan(…) / snan 不分大小写；NaN 的符号和 payload 也照 Swift 的来（只影响二进制位，不影响比较）。"""
    if s is None or s == "":
        return None
    if s[0] in " \t\n\v\f\r":
        return None
    if _DEC.match(s):
        return float(s)
    if _HEX.match(s):
        try:
            return float.fromhex(s)
        except OverflowError:
            return float("-inf") if s.startswith("-") else float("inf")
    if _INF.match(s):
        return float("-inf") if s.startswith("-") else float("inf")
    m = _NAN.match(s)
    if m:
        payload = 0
        seq = m.group(2)
        if seq:
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", seq):
                v = int(seq, 16)
            elif re.fullmatch(r"0[0-7]*", seq):
                v = int(seq, 8)
            elif re.fullmatch(r"[1-9][0-9]*", seq):
                v = int(seq, 10)
            else:
                v = 0
            payload = (v % (1 << 64)) & ((1 << 51) - 1)
        bits = (1 << 63 if m.group(1) == "-" else 0) | 0x7FF8000000000000 | payload
        return struct.unpack("<d", struct.pack("<Q", bits))[0]
    if _SNAN.match(s):
        return struct.unpack("<d", struct.pack("<Q", 0x7FF4000000000000))[0]
    return None


def swift_sorted(values):
    """values.sorted()：照抄 Swift 标准库的稳定排序（_stableSortImpl：找升降段 + 插入排序补到最短段长 + 归并），
    遇到 NaN（怎么比都是 False）时排出来的顺序也和 Swift 一样。没有 NaN 时和 Python 的 sorted() 结果相同。
    原文在 Xcode 的 Swift.swiftmodule/*.swiftinterface 里（Sort.swift 那几个 @inlinable 函数）。"""
    a = list(values)
    _stable_sort(a, lambda x, y: x < y)
    return a


def _min_merge_run_length(c):
    if c < (1 << 6):
        return c
    offset = c.bit_length() - 6            # (Int.bitWidth - 6) - c.leadingZeroBitCount
    mask = (1 << offset) - 1
    return (c >> offset) + (0 if (c & mask) == 0 else 1)


def _insertion_sort(a, lo, hi, sorted_end, less):
    while sorted_end != hi:
        i = sorted_end
        while True:
            j = i - 1
            if not less(a[i], a[j]):
                break
            a[i], a[j] = a[j], a[i]
            i = j
            if i == lo:
                break
        sorted_end += 1


def _find_next_run(a, start, less):
    n = len(a)
    previous = start
    current = start + 1
    if not current < n:
        return current, False
    descending = less(a[current], a[previous])
    while True:
        previous = current
        current += 1
        if not (current < n and descending == less(a[current], a[previous])):
            break
    return current, descending


def _reverse(a, lo, hi):
    f, l = lo, hi
    while f < l:
        l -= 1
        a[f], a[l] = a[l], a[f]
        f += 1


def _merge(a, low, mid, high, less):
    low_count, high_count = mid - low, high - mid
    if low_count < high_count:
        buf = a[low:mid]
        b_low, b_high = 0, low_count
        dest, src = low, mid
        while b_low < b_high and src < high:
            if less(a[src], buf[b_low]):
                a[dest] = a[src]
                src += 1
            else:
                a[dest] = buf[b_low]
                b_low += 1
            dest += 1
        a[dest:dest + (b_high - b_low)] = buf[b_low:b_high]
    else:
        buf = a[mid:high]
        b_low, b_high = 0, high_count
        dest_high, src_high, dest_low = high, mid, mid
        while b_high > b_low and src_high > low:
            dest_high -= 1
            if less(buf[b_high - 1], a[src_high - 1]):
                src_high -= 1
                a[dest_high] = a[src_high]
                dest_low -= 1
            else:
                b_high -= 1
                a[dest_high] = buf[b_high]
        a[dest_low:dest_low + (b_high - b_low)] = buf[b_low:b_high]


def _merge_runs(a, runs, i, less):
    low, middle, high = runs[i - 1][0], runs[i][0], runs[i][1]
    _merge(a, low, middle, high, less)
    runs[i - 1] = (low, high)
    del runs[i]


def _merge_top_runs(a, runs, less):
    def count(r):
        return r[1] - r[0]
    while len(runs) > 1:
        last = len(runs) - 1
        if last >= 3 and count(runs[last - 3]) <= count(runs[last - 2]) + count(runs[last - 1]):
            if count(runs[last - 2]) < count(runs[last]):
                last -= 1
        elif last >= 2 and count(runs[last - 2]) <= count(runs[last - 1]) + count(runs[last]):
            if count(runs[last - 2]) < count(runs[last]):
                last -= 1
        elif count(runs[last - 1]) <= count(runs[last]):
            pass
        else:
            break
        _merge_runs(a, runs, last, less)


def _stable_sort(a, less):
    n = len(a)
    min_run = _min_merge_run_length(n)
    if n <= min_run:
        if n:
            _insertion_sort(a, 0, n, 1, less)
        return
    runs = []
    start = 0
    while start < n:
        end, descending = _find_next_run(a, start, less)
        if descending:
            _reverse(a, start, end)
        if end < n and end - start < min_run:
            new_end = min(n, start + min_run)
            _insertion_sort(a, start, new_end, end, less)
            end = new_end
        runs.append((start, end))
        _merge_top_runs(a, runs, less)
        start = end
    while len(runs) > 1:
        _merge_runs(a, runs, len(runs) - 1, less)


def swift_int(s, bits=64):
    """Int(s) / Int32(s)：只认 [+-]数字，超出范围返回 None。"""
    if s is None or not _INT.match(s):
        return None
    value = int(s)
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if value < lo or value > hi:
        return None
    return value


def swift_rounded(x):
    """x.rounded()：四舍五入，.5 往远离 0 的方向（和 C 的 round() 一样）。返回 float。"""
    if math.isnan(x) or math.isinf(x):
        return x
    f = math.floor(x)
    diff = x - f  # 对 double 来说这一步是精确的
    if diff > 0.5:
        return float(f + 1)
    if diff < 0.5:
        return float(f)
    return float(f + 1) if x > 0 else float(f)


def swift_round_up(x):
    """x.rounded(.up)"""
    return float(math.ceil(x))


def swift_int_trunc(x):
    """Int(x)：往 0 的方向截断。"""
    return int(x)


def fmt2(x):
    """String(format: "%.2f", x)"""
    return "%.2f" % x


def first_lone_newline(text):
    """tail.firstIndex(of: "\\n")：Swift 按“字”找，\\r\\n 算一个字，所以紧跟在 \\r 后面的 \\n 找不到。返回下标或 None。"""
    start = 0
    while True:
        i = text.find("\n", start)
        if i < 0:
            return None
        if i == 0 or text[i - 1] != "\r":
            return i
        start = i + 1


def swift_min(a, b):
    """Swift.min(a, b)：b < a 时取 b，否则取 a。"""
    return b if b < a else a


def swift_max(a, b):
    """Swift.max(a, b)：b >= a 时取 b，否则取 a。"""
    return b if b >= a else a

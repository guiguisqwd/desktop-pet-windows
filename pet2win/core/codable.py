"""读写 JSON 时按 Swift 的 Codable 来：必填的键少了或者类型不对就整个读不出来（返回 None），
可选的键没有就是 None，写的时候 None 的键不写（Swift 的 encodeIfPresent）。只用标准库。"""


class DecodeError(Exception):
    pass


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def req_number(obj, key):
    if key not in obj or not _is_number(obj[key]):
        raise DecodeError(key)
    return float(obj[key])


def opt_number(obj, key):
    if obj.get(key) is None:
        return None
    if not _is_number(obj[key]):
        raise DecodeError(key)
    return float(obj[key])


def req_int(obj, key):
    v = obj.get(key)
    if key not in obj or not _is_int(v):
        raise DecodeError(key)
    return int(v)


def opt_int(obj, key):
    if obj.get(key) is None:
        return None
    if not _is_int(obj[key]):
        raise DecodeError(key)
    return int(obj[key])


def _is_int(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and v.is_integer()


def req_str(obj, key):
    if key not in obj or not isinstance(obj[key], str):
        raise DecodeError(key)
    return obj[key]


def req_bool(obj, key):
    if key not in obj or not isinstance(obj[key], bool):
        raise DecodeError(key)
    return obj[key]


def opt_bool(obj, key):
    if obj.get(key) is None:
        return None
    if not isinstance(obj[key], bool):
        raise DecodeError(key)
    return obj[key]


def req_dict(v):
    if not isinstance(v, dict):
        raise DecodeError("object")
    return v


def without_none(d):
    return {k: v for k, v in d.items() if v is not None}

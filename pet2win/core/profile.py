"""照翻 Pet2/Core/Profile.swift：profile.json，本机存的基本资料。只用标准库。
每项都可以“不想说”：数字项存 None，选项存 "na"。本机存年龄数字；导出时只写 age_band。"""
from dataclasses import dataclass
from typing import Optional

from .codable import opt_int, opt_number, req_dict, req_str, without_none
from .swiftcompat import swift_rounded

SEX_OPTIONS = ["female", "male", "other", "na"]
HANDEDNESS_OPTIONS = ["right", "left", "both", "na"]
SIT_HOURS_OPTIONS = ["<4", "4-6", "6-8", "8-10", ">10", "na"]
OCCUPATION_OPTIONS = ["student", "it", "design", "office", "health", "other", "na"]
NECK_HISTORY_OPTIONS = ["yes", "no", "na"]
COUNTRY_OPTIONS = ["US", "CN", "other", "na"]


@dataclass
class Profile:
    age: Optional[int] = None
    sex: str = "na"             # female / male / other / na
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    handedness: str = "na"      # right / left / both / na
    sit_hours: str = "na"       # <4 / 4-6 / 6-8 / 8-10 / >10 / na
    occupation: str = "na"      # student / it / design / office / health / other / na
    neck_history: str = "na"    # yes / no / na
    country: str = "na"         # CN / US / other / na
    us_state: str = "na"        # 两个字母的州代码，只在 US 时有

    @property
    def is_china(self):
        return self.country == "CN"

    @staticmethod
    def age_band(age):
        """18-24 / 25-34 / 35-44 / 45-54 / 55-64 / 65+；没填或未满 18 是 "na"。"""
        if age is None or not (age >= 18) or not (age < 130):
            return "na"
        if age < 25:
            return "18-24"
        if age < 35:
            return "25-34"
        if age < 45:
            return "35-44"
        if age < 55:
            return "45-54"
        if age < 65:
            return "55-64"
        return "65+"

    @staticmethod
    def round_to(value, step):
        """取整到最近的 step（身高 5 cm、体重 5 kg）。"""
        return int(swift_rounded(value / step) * step)

    def export_fields(self, health_consent):
        """导出用的资料：年龄换成分档；③ 没勾时去掉身高、体重、病史；身高体重取整到 5。"""
        out = {
            "age_band": Profile.age_band(self.age),
            "sex": self.sex,
            "handedness": self.handedness,
            "sit_hours": self.sit_hours,
            "occupation": self.occupation,
            "country": self.country,
            "us_state": self.us_state if self.country == "US" else "na",
        }
        if health_consent:
            out["height_cm"] = Profile.round_to(self.height_cm, 5) if self.height_cm is not None else "na"
            out["weight_kg"] = Profile.round_to(self.weight_kg, 5) if self.weight_kg is not None else "na"
            out["neck_history"] = self.neck_history
        return out

    def to_json(self):
        return without_none({"age": self.age, "sex": self.sex, "height_cm": self.height_cm, "weight_kg": self.weight_kg,
                             "handedness": self.handedness, "sit_hours": self.sit_hours, "occupation": self.occupation,
                             "neck_history": self.neck_history, "country": self.country, "us_state": self.us_state})

    @classmethod
    def from_json(cls, obj):
        obj = req_dict(obj)
        return cls(age=opt_int(obj, "age"), sex=req_str(obj, "sex"), height_cm=opt_number(obj, "height_cm"),
                   weight_kg=opt_number(obj, "weight_kg"), handedness=req_str(obj, "handedness"),
                   sit_hours=req_str(obj, "sit_hours"), occupation=req_str(obj, "occupation"),
                   neck_history=req_str(obj, "neck_history"), country=req_str(obj, "country"),
                   us_state=req_str(obj, "us_state"))

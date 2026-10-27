"""照翻 Pet2/Core/Drowsiness.swift：犯困、睡着只看“这个姿势离提醒门槛多远”。"""
from enum import Enum


class Drowsiness(Enum):
    AWAKE = "awake"
    DROWSY = "drowsy"
    ASLEEP = "asleep"

    @staticmethod
    def stage(progress):
        """progress：这个姿势离提醒门槛走到哪了（1 = 到门槛），来自 Brain.reminder_progress。"""
        if progress >= ASLEEP_FROM:
            return Drowsiness.ASLEEP
        if progress >= DROWSY_FROM:
            return Drowsiness.DROWSY
        return Drowsiness.AWAKE


DROWSY_FROM = 0.4
ASLEEP_FROM = 0.7

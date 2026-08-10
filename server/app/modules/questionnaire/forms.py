"""Formal daily questionnaire definitions and reusable validation.

This module has no FastAPI or database dependency. Other services, exports, or
local tools can import the same versioned forms without importing the web app.
Participant identity, local date, and Study Day are intentionally bound by the
calling service instead of being asked again in the questionnaire.
"""

from __future__ import annotations

import re
from typing import Any

FORM_VERSION = "formal_v1"

ALERTNESS_OPTIONS = [
    {"value": "extremely_alert", "label": "极度警醒"},
    {"value": "very_alert", "label": "非常警醒"},
    {"value": "alert", "label": "警醒"},
    {"value": "rather_alert", "label": "相对警醒"},
    {"value": "neutral", "label": "既不警醒但也不困倦"},
    {"value": "somewhat_sleepy", "label": "有点困倦"},
    {"value": "sleepy_easy", "label": "困倦，保持警醒不费力"},
    {"value": "sleepy_effort", "label": "困倦，保持警醒有点费力"},
    {"value": "very_sleepy", "label": "非常困倦"},
]

DEVICE_STATUS_OPTIONS = [
    {"value": "normal", "label": "正常"},
    {"value": "brief_interruption", "label": "有短时中断"},
    {"value": "obvious_abnormality", "label": "明显异常"},
    {"value": "uncertain", "label": "不确定"},
]

FORMS = {
    "morning": {
        "key": "morning",
        "title": "每日记录1-晨起后填写",
        "description": "起床后填写；编号、日期和 Study Day 已由系统自动绑定",
        "questions": [
            {"key": "bedtime", "source_number": 3, "type": "time", "label": "昨晚几点入睡", "required": True},
            {"key": "wake_time", "source_number": 4, "type": "time", "label": "今天醒来的时间", "required": True},
            {"key": "alertness", "source_number": 5, "type": "choice", "label": "请选择能描述你现在状态的选项", "options": ALERTNESS_OPTIONS, "required": True},
            {"key": "sleep_quality", "source_number": 6, "type": "scale", "label": "昨晚整体睡眠质量如何？", "min": -3, "max": 3, "low": "非常差", "high": "非常好", "required": True},
            {"key": "sleep_recovery", "source_number": 7, "type": "scale", "label": "今天醒来后是否精神恢复？", "min": -3, "max": 3, "low": "根本没有", "high": "完全", "required": True},
            {"key": "sleep_continuity", "source_number": 8, "type": "scale", "label": "昨晚睡眠是否安稳、连续？", "min": -3, "max": 3, "low": "非常不安", "high": "非常安稳", "required": True},
            {"key": "sleep_sufficiency", "source_number": 9, "type": "scale", "label": "昨晚的睡眠是否足够？", "min": -3, "max": 3, "low": "绝对太少", "high": "非常足够", "required": True},
            {"key": "sleep_onset_ease", "source_number": 10, "type": "scale", "label": "昨晚的入睡是否容易？", "min": -3, "max": 3, "low": "非常困难", "high": "非常容易", "required": True},
            {"key": "wake_ease", "source_number": 11, "type": "scale", "label": "早晨醒来是否容易？", "min": -3, "max": 3, "low": "非常困难", "high": "非常容易", "required": True},
            {
                "key": "sleep_influences",
                "source_number": 12,
                "type": "multichoice",
                "label": "昨晚是否有明显影响睡眠的情况？",
                "options": [
                    {"value": "none", "label": "没有"},
                    {"value": "caffeine_or_medication", "label": "服用咖啡因/药物"},
                    {"value": "environment", "label": "环境影响（如噪音等）"},
                    {"value": "work_or_life", "label": "工作/生活事务"},
                    {"value": "illness", "label": "生病"},
                    {"value": "other", "label": "其他影响"},
                ],
                "min_selections": 1,
                "exclusive": ["none"],
                "required": True,
            },
        ],
    },
    "evening": {
        "key": "evening",
        "title": "每日记录2-入睡前填写",
        "description": "入睡前填写；编号、日间活动日期和 Study Day 已由系统自动绑定",
        "questions": [
            {"key": "alertness", "source_number": 3, "type": "choice", "label": "请选择能描述你现在状态的选项", "options": ALERTNESS_OPTIONS, "required": True},
            {"key": "day_energy", "source_number": 4, "type": "scale", "label": "请根据今天起床后到现在的整体感受，选择最符合您今日状态的选项", "min": -3, "max": 3, "low": "精疲力竭", "high": "精力充沛", "required": True},
            {"key": "day_mood", "source_number": 5, "type": "scale", "label": "请根据今天起床后到现在的整体感受，选择最符合您今日状态的选项", "min": -3, "max": 3, "low": "悲伤", "high": "快乐", "required": True},
            {"key": "day_activation", "source_number": 6, "type": "scale", "label": "请根据今天起床后到现在的整体感受，选择最符合您今日状态的选项", "min": -3, "max": 3, "low": "放松", "high": "兴奋", "required": True},
            {
                "key": "nap_duration",
                "source_number": 7,
                "type": "choice",
                "label": "今日午睡总时长大约为？",
                "options": [
                    {"value": "none", "label": "无"},
                    {"value": "within_30_minutes", "label": "30分钟以内"},
                    {"value": "30_to_60_minutes", "label": "30-60分钟"},
                    {"value": "over_60_minutes", "label": "60分钟以上"},
                ],
                "required": True,
            },
            {
                "key": "device_status",
                "source_number": 8,
                "type": "matrix",
                "label": "今天设备记录是否基本正常？",
                "rows": [
                    {"key": "gps", "label": "GPS记录"},
                    {"key": "lighting", "label": "光照记录"},
                ],
                "options": DEVICE_STATUS_OPTIONS,
                "required": True,
            },
        ],
    },
}


def list_forms() -> list[dict[str, Any]]:
    """Return formal forms in participant-task order."""
    return [FORMS["morning"], FORMS["evening"]]


def get_form(form_key: str) -> dict[str, Any] | None:
    return FORMS.get(form_key)


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _option_values(question: dict[str, Any]) -> set[str]:
    return {str(option["value"]) for option in question.get("options", [])}


def validate_answers(form: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one final submission against a form definition."""
    if not isinstance(answers, dict):
        raise ValueError("问卷答案格式无效")
    normalized: dict[str, Any] = {}
    for question in form["questions"]:
        key = question["key"]
        value = answers.get(key)
        if question.get("required") and _missing(value):
            raise ValueError(f"请完成：{question['label']}")
        if _missing(value):
            normalized[key] = ""
            continue

        question_type = question["type"]
        if question_type in {"scale", "number"}:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"无效答案：{question['label']}") from exc
            if number < float(question.get("min", number)) or number > float(question.get("max", number)):
                raise ValueError(f"答案超出范围：{question['label']}")
            normalized[key] = int(number) if number.is_integer() else number
        elif question_type == "time":
            text = str(value).strip()
            match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", text)
            if not match:
                raise ValueError(f"时间格式无效：{question['label']}")
            normalized[key] = text
        elif question_type == "choice":
            selected = str(value)
            if selected not in _option_values(question):
                raise ValueError(f"无效答案：{question['label']}")
            normalized[key] = selected
        elif question_type == "multichoice":
            if not isinstance(value, list):
                raise ValueError(f"无效答案：{question['label']}")
            selected = list(dict.fromkeys(str(item) for item in value))
            if not set(selected) <= _option_values(question):
                raise ValueError(f"无效答案：{question['label']}")
            if len(selected) < int(question.get("min_selections", 0)):
                raise ValueError(f"请至少选择一项：{question['label']}")
            exclusive = set(question.get("exclusive", []))
            if exclusive.intersection(selected) and len(selected) > 1:
                raise ValueError(f"“没有”不能与其他选项同时选择：{question['label']}")
            normalized[key] = selected
        elif question_type == "matrix":
            if not isinstance(value, dict):
                raise ValueError(f"无效答案：{question['label']}")
            allowed = _option_values(question)
            row_keys = {row["key"] for row in question.get("rows", [])}
            if set(value) != row_keys or any(str(selected) not in allowed for selected in value.values()):
                raise ValueError(f"请完成每一行：{question['label']}")
            normalized[key] = {row["key"]: str(value[row["key"]]) for row in question["rows"]}
        else:
            text = str(value).strip()
            if len(text) > 2000:
                raise ValueError(f"文本过长：{question['label']}")
            normalized[key] = text
    return normalized


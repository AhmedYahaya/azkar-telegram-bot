"""Telegram message builders."""
from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping

from config import CONFIG, QURAN_DAILY_PAGES, ROLE_NAMES

ITEM_LABELS = {
    "morning": "أذكار الصباح",
    "evening": "أذكار المساء",
    "sleep": "أذكار النوم",
}

MEDICATION_LABELS = {
    "ahmed_pre_lunch": "دواء قبل الغداء",
    "janna_pre_lunch": "دواء قبل الغداء",
    "janna_after_lunch": "دواء بعد الغداء",
}

MEDICATION_ROLE = {
    "ahmed_pre_lunch": "ahmed",
    "janna_pre_lunch": "janna",
    "janna_after_lunch": "janna",
}


def user_name(user_id: int, participants: Mapping[str, object] | None = None) -> str:
    if participants:
        for row in participants.values():
            try:
                if int(row["user_id"]) == user_id:
                    return str(row["display_name"])
            except (KeyError, TypeError, ValueError):
                pass
    return f"المستخدم {user_id}"


def status_line(statuses: dict[int, str], user_id: int, name: str, label: str) -> str:
    status = statuses.get(user_id)
    if status == "completed":
        return f"✅ {name} أنهى {label}"
    if status == "already_taken":
        return f"✅ {name} كان أخذ {label} بالفعل"
    if status == "snoozed":
        return f"⏰ {name} أجّل {label} 15 دقيقة"
    return f"⏳ {name} لم يكمل {label}"


def participant_lines(
    statuses: dict[int, str], participants: Mapping[str, object], label: str
) -> list[str]:
    lines: list[str] = []
    for role in ("ahmed", "janna"):
        row = participants.get(role)
        if row:
            lines.append(
                status_line(statuses, int(row["user_id"]), str(row["display_name"]), label)
            )
        else:
            lines.append(f"⏳ {ROLE_NAMES[role]} لم يشغّل الخاص مع البوت بعد")
    return lines


def azkar_text(
    item_key: str,
    statuses: dict[int, str],
    streak: int,
    participants: Mapping[str, object],
) -> str:
    label = ITEM_LABELS[item_key]
    lines = [
        f"✨ {label}",
        "",
        *participant_lines(statuses, participants, label),
        "",
        f"🔥 الستريك المشترك: {streak} يوم",
    ]
    if item_key == "sleep":
        lines.append("🎧 أزرار الصوت تحت الرسالة لتشغيل سورة الملك أو أذكار النوم.")
    return "\n".join(lines)


def medication_text(
    item_key: str,
    status: str | None,
    *,
    retry: bool = False,
    owner_name: str | None = None,
) -> str:
    role = MEDICATION_ROLE[item_key]
    name = owner_name or ROLE_NAMES[role]
    label = MEDICATION_LABELS[item_key]
    if item_key == "ahmed_pre_lunch":
        time_text = "2:00 م"
    elif item_key == "janna_pre_lunch":
        time_text = "1:00 م" if retry else "12:00 م"
    else:
        time_text = "4:00 م"

    if status == "completed":
        state = f"✅ {name} أنهى {label}"
    elif status == "already_taken":
        state = f"✅ {name} كان أخذ {label} بالفعل"
    elif status == "snoozed":
        state = f"⏰ {name} أجّل {label} 15 دقيقة"
    else:
        state = f"⏳ {name} لم يكمل {label}"

    if item_key == "janna_pre_lunch" and retry:
        header = f"🔔 إعادة تذكير — {label} — جنى"
        note = "جنى لسه ما سجلتش إن الدواء اتاخد، فده تذكير الساعة 1:00 م لنفس جرعة الـ12:00."
    elif role == "janna":
        header = f"💊 {label} — جنى"
        note = "التذكير ده مخصص لجنى فقط."
    else:
        header = f"💊 {label} — أحمد"
        note = "التذكير ده مخصص لأحمد فقط."

    return "\n".join([
        header,
        f"⏰ الموعد: {time_text}",
        note,
        "",
        state,
    ])


def quran_range_label(page_numbers: list[int]) -> str:
    if not page_numbers:
        return "—"
    if page_numbers == list(range(page_numbers[0], page_numbers[-1] + 1)):
        return f"{page_numbers[0]}–{page_numbers[-1]}"
    segments: list[str] = []
    start = prev = page_numbers[0]
    for page in page_numbers[1:]:
        if page != prev + 1:
            segments.append(f"{start}–{prev}")
            start = page
        prev = page
    segments.append(f"{start}–{prev}")
    return " + ".join(segments)


def quran_text(
    statuses: dict[int, str],
    participants: Mapping[str, object],
    page_numbers: list[int],
    carried_over: bool,
) -> str:
    range_label = quran_range_label(page_numbers)
    lines = [
        f"📖 الورد القرآني — {QURAN_DAILY_PAGES} صفحات",
        f"📚 الصفحات: {range_label}",
        "",
    ]
    if carried_over:
        lines += [
            "⚠️ الورد غير منتهٍ من أمس.",
            "تابعوا نفس الـ10 صفحات قبل ما ننتقل للصفحات التالية.",
            "",
        ]
    lines.extend(participant_lines(statuses, participants, "الورد"))
    return "\n".join(lines)


def tasbeeh_text(goal, owner_name: str | None = None, creator_name: str | None = None) -> str:
    if goal["scope"] == "shared":
        scope_text = "🤝 هدف مشترك بين أحمد وجنى"
    else:
        scope_text = f"👤 هدف شخصي — {owner_name or 'صاحبه'}"
        if creator_name and creator_name != owner_name:
            scope_text += f"\n🎯 حدده: {creator_name}"
    remaining = max(int(goal["target"]) - int(goal["count"]), 0)
    return "\n".join([
        f"📿 هدف تسبيح #{goal['id']}",
        scope_text,
        f"الهدف: {goal['title']} — {goal['target']}",
        f"العدد الحالي: {goal['count']}/{goal['target']}",
        f"المتبقي: {remaining}",
    ])


def weekly_badge(average: float) -> str:
    if average >= 0.90:
        return "🏅 وسام الثبات الجميل"
    if average >= 0.75:
        return "🌟 وسام الاستمرار"
    if average >= 0.50:
        return "💛 وسام خطوة بخطوة"
    return "🌱 وسام البداية الجديدة"


def fmt_percent(value: float) -> str:
    return f"{round(value * 100):d}%"


def focus_until_text(until: datetime) -> str:
    return until.astimezone(CONFIG.timezone).strftime("%I:%M %p").lstrip("0")

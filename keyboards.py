"""Inline keyboard builders."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def azkar_keyboard(item_key: str, item_date: str, include_audio: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("أنهيت الأذكار ✅", callback_data=f"azkar:{item_key}:{item_date}:done")]]
    if include_audio:
        rows.append([
            InlineKeyboardButton("▶️ سورة الملك", callback_data="audio:mulk"),
            InlineKeyboardButton("▶️ أذكار النوم", callback_data="audio:sleep"),
        ])
    return InlineKeyboardMarkup(rows)


def medication_keyboard(item_key: str, item_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("أخذت الدواء 💊", callback_data=f"med:{item_key}:{item_date}:take")],
        [
            InlineKeyboardButton("تأجيل 15 دقيقة ⏰", callback_data=f"med:{item_key}:{item_date}:snooze"),
            InlineKeyboardButton("أخذته بالفعل ✅", callback_data=f"med:{item_key}:{item_date}:already"),
        ],
    ])


def quran_keyboard(item_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("أنهيت الورد 📖", callback_data=f"quran:{item_date}:done")]
    ])


def tasbeeh_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("+10", callback_data=f"tas:{goal_id}:10"),
            InlineKeyboardButton("+50", callback_data=f"tas:{goal_id}:50"),
        ]
    ])


def snoozed_med_keyboard(item_key: str, item_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("أخذته الآن 💊", callback_data=f"med:{item_key}:{item_date}:take_after_snooze")]
    ])

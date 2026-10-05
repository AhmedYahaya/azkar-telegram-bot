"""All recurring and one-off jobs used by the bot."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta

import httpx
from telegram.ext import Application, ContextTypes

from config import (
    AZKAR_IMAGES,
    CONFIG,
    QURAN_API_BASE_URL,
    QURAN_DAILY_PAGES,
    QURAN_EDITION,
    QURAN_FIRST_PAGE,
    QURAN_TOTAL_PAGES,
    SCHEDULE,
    azkar_image_path,
)
from formatters import (
    ITEM_LABELS,
    MEDICATION_LABELS,
    MEDICATION_ROLE,
    azkar_text,
    fmt_percent,
    medication_text,
    quran_range_label,
    quran_text,
    weekly_badge,
    user_name,
)
from keyboards import (
    azkar_keyboard,
    medication_keyboard,
    quran_keyboard,
)

AZKAR_ITEMS = {
    "morning": "أذكار الصباح",
    "evening": "أذكار المساء",
    "sleep": "أذكار النوم",
}

# The 13:00 Janna reminder is a retry of the same task, not a second medication.
MED_SCHEDULES = (
    ("ahmed_pre_lunch", "ahmed_medication", False),
    ("janna_pre_lunch", "janna_pre_lunch", False),
    ("janna_pre_lunch", "janna_pre_lunch_retry", True),
    ("janna_after_lunch", "janna_after_lunch", False),
)


async def schedule_all(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        raise RuntimeError("JobQueue is unavailable. Install python-telegram-bot[job-queue].")

    for item_key in ("morning", "evening", "sleep"):
        hour, minute = SCHEDULE[item_key]
        jq.run_daily(
            send_azkar_reminder,
            time(hour=hour, minute=minute),
            data={"item_key": item_key},
            name=f"azkar_{item_key}",
        )

    for item_key, schedule_key, retry in MED_SCHEDULES:
        hour, minute = SCHEDULE[schedule_key]
        callback = send_janna_medication_retry if retry else send_medication_reminder
        jq.run_daily(
            callback,
            time(hour=hour, minute=minute),
            data={"item_key": item_key},
            name=(f"med_{item_key}_retry" if retry else f"med_{item_key}"),
        )

    hour, minute = SCHEDULE["quran"]
    jq.run_daily(send_quran_reminder, time(hour=hour, minute=minute), name="quran_daily")

    hour, minute = SCHEDULE["weekly_digest"]
    jq.run_daily(send_weekly_digest, time(hour=hour, minute=minute), days=(5,), name="weekly_digest")

    hour, minute = SCHEDULE["streak_check"]
    jq.run_daily(check_yesterday_streak, time(hour=hour, minute=minute), name="streak_check")

    now = datetime.now(CONFIG.timezone)
    db = app.bot_data["db"]

    # Reconcile yesterday's shared streak shortly after startup, so a restart after
    # midnight does not make the bot wait until the next day to notice a missed day.
    jq.run_once(
        check_yesterday_streak,
        when=1.0,
        name="startup_streak_check",
        job_kwargs={"replace_existing": True},
    )

    # Restore scheduled medication snoozes after a restart.
    for row in await db.get_active_snoozes():
        try:
            remind_at = datetime.fromisoformat(row["remind_at"])
        except ValueError:
            continue
        delay = max((remind_at - now).total_seconds(), 0.1)
        jq.run_once(
            medication_snooze_job,
            when=delay,
            data=dict(row),
            name=f"med_snooze_{row['user_id']}_{row['item_key']}_{row['item_date']}",
            user_id=row["user_id"],
            job_kwargs={"replace_existing": True},
        )

    # Restore an active Focus session after a restart.
    # The focus record contains the original group notice so the user keeps the same guard message.
    from handlers import focus_finished_job

    for row in await db.get_active_focus():
        try:
            focus_until = datetime.fromisoformat(row["focus_until"])
        except ValueError:
            await db.clear_focus(int(row["user_id"]))
            continue
        if focus_until <= now:
            await db.clear_focus(int(row["user_id"]))
            continue
        user_id = int(row["user_id"])
        user = await db.get_user(user_id)
        name = user["display_name"] if user else "المستخدم"
        delay = max((focus_until - now).total_seconds(), 0.1)
        jq.run_once(
            focus_finished_job,
            when=delay,
            data={"user_id": user_id, "focus_until": row["focus_until"], "name": name},
            name=f"focus_end_{user_id}",
            user_id=user_id,
            job_kwargs={"replace_existing": True},
        )


async def send_azkar_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    app, db = context.application, context.application.bot_data["db"]
    item_key = context.job.data["item_key"]
    today = datetime.now(CONFIG.timezone).date().isoformat()
    participants = await db.get_participants()
    ids = await db.get_participant_ids()
    statuses = await db.get_statuses(today, "azkar", item_key, ids)
    streak = (await db.get_streak())["current_streak"]
    text = azkar_text(item_key, statuses, streak, participants)

    from keyboards import azkar_keyboard
    markup = azkar_keyboard(item_key, today, include_audio=item_key == "sleep")

    image_path = azkar_image_path(item_key)
    if image_path and image_path.exists():
        with image_path.open("rb") as photo:
            message = await app.bot.send_photo(
                chat_id=CONFIG.group_chat_id,
                photo=photo,
                caption=text,
                reply_markup=markup,
            )
        await db.set_reminder(today, "azkar", item_key, CONFIG.group_chat_id, message.message_id, "photo")
    else:
        if image_path:
            app.logger.warning("Azkar image not found for %s: %s", item_key, image_path)
        message = await app.bot.send_message(
            chat_id=CONFIG.group_chat_id,
            text=text,
            reply_markup=markup,
        )
        await db.set_reminder(today, "azkar", item_key, CONFIG.group_chat_id, message.message_id, "text")


async def _target_role_id(db, role: str) -> int | None:
    participants = await db.get_participants()
    row = participants.get(role)
    return int(row["user_id"]) if row else None


async def send_medication_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    app, db = context.application, context.application.bot_data["db"]
    item_key = context.job.data["item_key"]
    target_role = MEDICATION_ROLE[item_key]
    target_id = await _target_role_id(db, target_role)
    today = datetime.now(CONFIG.timezone).date().isoformat()

    status = None
    if target_id is not None:
        status = (await db.get_statuses(today, "med", item_key, (target_id,))).get(target_id)

    message = await app.bot.send_message(
        chat_id=CONFIG.group_chat_id,
        text=medication_text(item_key, status),
        reply_markup=medication_keyboard(item_key, today),
    )
    await db.set_reminder(today, "med", item_key, CONFIG.group_chat_id, message.message_id, "text")

    # Also send a private heads-up, but this does not duplicate the shared group reminder.
    if target_id is not None:
        await _safe_pm(
            app,
            target_id,
            f"💊 تذكير: {MEDICATION_LABELS[item_key]}.",
        )


async def send_janna_medication_retry(context: ContextTypes.DEFAULT_TYPE) -> None:
    """At 13:00, remind Janna again only if the 12:00 dose is unfinished."""
    app, db = context.application, context.application.bot_data["db"]
    item_key = "janna_pre_lunch"
    today = datetime.now(CONFIG.timezone).date().isoformat()
    janna_id = await _target_role_id(db, "janna")
    if janna_id is None:
        return

    status = (await db.get_statuses(today, "med", item_key, (janna_id,))).get(janna_id)
    if status in {"completed", "already_taken"}:
        return

    text = medication_text(item_key, status, retry=True)
    markup = medication_keyboard(item_key, today)
    existing = await db.get_reminder(today, "med", item_key)

    if existing:
        try:
            await app.bot.edit_message_text(
                chat_id=existing["chat_id"],
                message_id=existing["message_id"],
                text=text,
                reply_markup=markup,
            )
            await _safe_pm(app, janna_id, "🔔 إعادة تذكير: لسه ما سجلتيش دواء قبل الغداء.")
            return
        except Exception as exc:  # Telegram can reject edit if old message was removed.
            app.logger.warning("Could not edit Janna medication reminder, sending a new one: %s", exc)

    message = await app.bot.send_message(
        chat_id=CONFIG.group_chat_id,
        text=text,
        reply_markup=markup,
    )
    await db.set_reminder(today, "med", item_key, CONFIG.group_chat_id, message.message_id, "text")
    await _safe_pm(app, janna_id, "🔔 إعادة تذكير: لسه ما سجلتيش دواء قبل الغداء.")


async def _safe_pm(app, user_id: int, text: str) -> None:
    try:
        await app.bot.send_message(chat_id=user_id, text=text)
    except Exception as exc:
        app.logger.info("Could not send PM to %s: %s", user_id, exc)


async def fetch_quran_page(client: httpx.AsyncClient, page_number: int) -> str:
    """Fetch one Uthmani Quran page. Only one page is held in memory at a time."""
    url = f"{QURAN_API_BASE_URL}/page/{page_number}/{QURAN_EDITION}"
    response = await client.get(url)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    ayahs = data.get("ayahs") or []
    if not ayahs:
        raise ValueError(f"No ayahs returned for Quran page {page_number}")

    lines = [f"📖 صفحة {page_number}"]
    current_surah: str | None = None
    for ayah in ayahs:
        surah = (ayah.get("surah") or {}).get("name")
        if surah and surah != current_surah:
            current_surah = surah
            lines.append(f"\n【{surah}】")
        text = str(ayah.get("text", "")).strip()
        number = ayah.get("numberInSurah")
        if text:
            lines.append(f"{text} ۝{number}" if number is not None else text)
    return "\n".join(lines)


def _split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    """Split long page text safely under Telegram's normal message limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in text.split("\n"):
        extra = len(paragraph) + (1 if current else 0)
        if current and length + extra > limit:
            chunks.append("\n".join(current))
            current = [paragraph]
            length = len(paragraph)
        else:
            current.append(paragraph)
            length += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


async def send_quran_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch today's 10 pages online and send them as lightweight text messages."""
    app, db = context.application, context.application.bot_data["db"]
    today = datetime.now(CONFIG.timezone).date().isoformat()

    # Do not fetch the same 10 pages twice on the same day. A failed fetch does not
    # create the reminder row, so /quran can safely retry after a temporary outage.
    existing_reminder = await db.get_reminder(today, "quran", "daily")
    if existing_reminder:
        return

    participant_ids = await db.get_participant_ids()
    assignment = await db.prepare_quran_assignment(
        today, participant_ids, QURAN_FIRST_PAGE, QURAN_DAILY_PAGES
    )
    start_page = int(assignment["start_page"])
    page_numbers = _wrap_page_numbers(start_page, QURAN_DAILY_PAGES)
    carried_over = bool(assignment["carried_over"])

    participants = await db.get_participants()
    statuses = await db.get_statuses(today, "quran", "daily", participant_ids)
    intro = quran_text(statuses, participants, page_numbers, carried_over)
    try:
        await app.bot.send_message(chat_id=CONFIG.group_chat_id, text=intro)
        async with httpx.AsyncClient(timeout=20.0) as client:
            for index, page_number in enumerate(page_numbers):
                page_text = await fetch_quran_page(client, page_number)
                chunks = _split_telegram_text(page_text)
                for chunk in chunks:
                    await app.bot.send_message(chat_id=CONFIG.group_chat_id, text=chunk)
                # Keep requests sequential and modest; the API applies a soft per-second rate limit.
                if index < len(page_numbers) - 1:
                    await asyncio.sleep(0.15)

        message = await app.bot.send_message(
            chat_id=CONFIG.group_chat_id,
            text="✅ صفحات الورد فوق. لما تخلصوا الـ10 صفحات كلكم اضغطوا الزر:",
            reply_markup=quran_keyboard(today),
        )
        await db.set_reminder(today, "quran", "daily", CONFIG.group_chat_id, message.message_id, "text")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        app.logger.exception("Quran API fetch failed")
        await app.bot.send_message(
            chat_id=CONFIG.group_chat_id,
            text=(
                "⚠️ مقدرتش أجلب صفحات الورد من خدمة القرآن الآن.\n"
                "الورد لم يُعتبر مكتملًا؛ جرّب /quran بعد رجوع الاتصال.\n\n"
                f"التفاصيل التقنية: {exc}"
            ),
        )


def _wrap_page_numbers(start_page: int, count: int) -> list[int]:
    if QURAN_TOTAL_PAGES <= 0:
        return []
    return [((start_page - 1 + offset) % QURAN_TOTAL_PAGES) + 1 for offset in range(count)]


async def send_weekly_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    app, db = context.application, context.application.bot_data["db"]
    today = datetime.now(CONFIG.timezone).date()
    end, start = today - timedelta(days=1), today - timedelta(days=7)
    participants = await db.get_participants()
    rows = ["📊 التقرير الأسبوعي المشترك", f"من {start:%Y-%m-%d} إلى {end:%Y-%m-%d}", ""]
    all_rates: list[float] = []

    for role in ("ahmed", "janna"):
        participant = participants.get(role)
        if not participant:
            rows += [f"👤 {role}", "  • لم يفعّل الخاص بعد", ""]
            continue

        user_id, name = int(participant["user_id"]), str(participant["display_name"])
        azkar_counts = await asyncio.gather(*[
            db.weekly_count(start.isoformat(), end.isoformat(), "azkar", key, user_id)
            for key in AZKAR_ITEMS
        ])
        quran_done = await db.weekly_count(start.isoformat(), end.isoformat(), "quran", "daily", user_id)
        azkar_done = sum(azkar_counts)
        azkar_rate = azkar_done / 21
        quran_rate = quran_done / 7

        med_rate: float | None
        if role == "ahmed":
            med_done = await db.weekly_count(
                start.isoformat(), end.isoformat(), "med", "ahmed_pre_lunch", user_id
            )
            med_rate = med_done / 7
            med_detail = f"{med_done}/7"
        else:
            pre_done = await db.weekly_count(
                start.isoformat(), end.isoformat(), "med", "janna_pre_lunch", user_id
            )
            after_done = await db.weekly_count(
                start.isoformat(), end.isoformat(), "med", "janna_after_lunch", user_id
            )
            med_done = pre_done + after_done
            med_rate = med_done / 14
            med_detail = f"{med_done}/14 (قبل الغداء {pre_done}/7 + بعد الغداء {after_done}/7)"

        avg = (azkar_rate + quran_rate + med_rate) / 3
        all_rates.append(avg)
        rows += [
            f"👤 {name}",
            f"  • الأذكار: {fmt_percent(azkar_rate)}",
            f"  • الورد: {fmt_percent(quran_rate)}",
            f"  • الأدوية: {fmt_percent(med_rate)} — {med_detail}",
            f"  • المتوسط: {fmt_percent(avg)}",
            "",
        ]

    joint = sum(all_rates) / len(all_rates) if all_rates else 0
    streak = (await db.get_streak())["current_streak"]
    rows += [
        f"🔥 الستريك الحالي: {streak} يوم",
        weekly_badge(joint),
        "استمروا خطوة بخطوة، والمهم إنكم تراجعوا بعض وتكملوا معًا 🤍",
    ]
    await app.bot.send_message(chat_id=CONFIG.group_chat_id, text="\n".join(rows))


async def check_yesterday_streak(context: ContextTypes.DEFAULT_TYPE) -> None:
    app, db = context.application, context.application.bot_data["db"]
    today = datetime.now(CONFIG.timezone).date()
    yesterday = today - timedelta(days=1)
    ids = await db.get_participant_ids()
    if len(ids) != 2 or await db.day_joint_azkar_complete(yesterday.isoformat(), ids):
        return
    state = await db.get_streak()
    if state["current_streak"] <= 0 or state["last_break_notified_date"] == yesterday.isoformat():
        return
    await db.update_streak(0, state["last_success_date"], yesterday.isoformat())
    await app.bot.send_message(
        chat_id=CONFIG.group_chat_id,
        text="💔 الستريك المشترك اتكسر لأن أذكار أمس ما اكتملتش من الطرفين. نبدأ من جديد النهارده 🤍",
    )


async def update_streak_after_completion(
    context: ContextTypes.DEFAULT_TYPE, item_date: str
) -> None:
    db = context.application.bot_data["db"]
    ids = await db.get_participant_ids()
    if len(ids) != 2:
        return
    if not await db.day_joint_azkar_complete(item_date, ids):
        return

    state = await db.get_streak()
    if state["last_success_date"] == item_date:
        return

    current = int(state["current_streak"])
    last_date = state["last_success_date"]
    current_date = datetime.fromisoformat(item_date).date()
    previous_date = (current_date - timedelta(days=1)).isoformat()
    new_streak = current + 1 if last_date == previous_date else 1
    await db.update_streak(new_streak, item_date, state["last_break_notified_date"])


async def medication_snooze_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    user_id = int(data["user_id"])
    item_key = str(data["item_key"])
    item_date = str(data["item_date"])
    db = context.application.bot_data["db"]

    row = await db.get_snooze(user_id, item_date, item_key)
    if not row or not row["active"]:
        return

    status = (await db.get_statuses(item_date, "med", item_key, (user_id,))).get(user_id)
    if status in {"completed", "already_taken"}:
        await db.deactivate_snooze(user_id, item_date, item_key)
        return

    await db.deactivate_snooze(user_id, item_date, item_key)
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⏰ تذكير تاني: {MEDICATION_LABELS[item_key]} — خدتَه؟",
        )
    except Exception:
        pass

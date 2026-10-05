"""Telegram command and callback handlers."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import CONFIG, FOCUS_DEFAULT_MINUTES, SNOOZE_MINUTES, QURAN_DAILY_PAGES, QURAN_TOTAL_PAGES, ROLE_NAMES
from formatters import (
    ITEM_LABELS,
    MEDICATION_LABELS,
    MEDICATION_ROLE,
    azkar_text,
    focus_until_text,
    medication_text,
    quran_range_label,
    quran_text,
    tasbeeh_text,
    user_name,
)
from keyboards import medication_keyboard, quran_keyboard, snoozed_med_keyboard, tasbeeh_keyboard
from scheduler import medication_snooze_job, update_streak_after_completion


def quran_page_numbers(start_page: int, count: int = QURAN_DAILY_PAGES) -> list[int]:
    if QURAN_TOTAL_PAGES <= 0:
        return []
    return [((start_page - 1 + offset) % QURAN_TOTAL_PAGES) + 1 for offset in range(count)]


async def ensure_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None:
        return False
    db = context.application.bot_data["db"]
    if await db.get_user_role(user.id):
        return True
    return await remember_user(update, context)


async def other_user_id(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> int | None:
    db = context.application.bot_data["db"]
    role = await db.get_user_role(user_id)
    other_role = "janna" if role == "ahmed" else "ahmed"
    participants = await db.get_participants()
    row = participants.get(other_role)
    return int(row["user_id"]) if row else None


def local_now() -> datetime:
    return datetime.now(CONFIG.timezone)


def today_str() -> str:
    return local_now().date().isoformat()


async def current_user_name(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    role = await context.application.bot_data["db"].get_user_role(user_id)
    return ROLE_NAMES.get(role, "المستخدم")


async def remember_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None:
        return False
    db = context.application.bot_data["db"]
    role = await db.get_user_role(user.id) or CONFIG.role_for_username(user.username)
    if role not in ROLE_NAMES:
        return False
    await db.upsert_user(user.id, ROLE_NAMES[role], user.username, role)
    return True


async def send_pm(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    *,
    respect_focus: bool = True,
    **kwargs: Any,
) -> bool:
    db = context.application.bot_data["db"]
    focus = await db.get_focus_until(user_id) if respect_focus else None
    if focus:
        try:
            until = datetime.fromisoformat(focus)
            if until > local_now():
                return False
        except ValueError:
            pass
    try:
        await context.bot.send_message(chat_id=user_id, text=text, **kwargs)
        return True
    except (Forbidden, TelegramError):
        return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await remember_user(update, context):
        return
    user_id = update.effective_user.id
    db = context.application.bot_data["db"]
    role = await db.get_user_role(user_id)
    name = ROLE_NAMES.get(role, "المستخدم")
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"أهلًا {name} 🤍\n\n"
            "البوت متصل. استخدم /help لمعرفة الأوامر، والـPM عندي جاهز للتنبيهات الشخصية."
        ),
    )


async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        await update.effective_message.reply_text(f"Telegram User ID: {update.effective_user.id}")


async def chat_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not await ensure_authorized(update, context):
        return
    await update.effective_message.reply_text(f"Chat ID: {update.effective_chat.id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    await update.effective_message.reply_text(
        """📚 دليل البوت — أحمد وجنى

/start
تشغيل البوت وتسجيل حسابك.

/status
يعرض حالتك اليوم.

/report
يبعت التقرير الأسبوعي للجروب فورًا.

🌅 الأذكار والورد
الأذكار والصور والتسجيل تتم تلقائيًا حسب المواعيد.
/quran
يجيب ورد اليوم الآن، ولو ورد اليوم اتبعت بالفعل مش هيجلبه مرة ثانية.

💭 الخاطرة
/quote الخاطرة هنا
تتحفظ للطرف الآخر وتوصله عند إنهاء ذكر.

💌 رسالة أحمد لجنى
/hidden الرسالة هنا
تظهر لجنى مرة واحدة عند إنهاء أذكار الصباح، ثم تُمسح من البوت.

💊 الأدوية
• أحمد: 2:00 م — دواء قبل الغداء
• جنى: 12:00 م — دواء قبل الغداء
• جنى: 1:00 م — إعادة تذكير لنفس جرعة الـ12:00 لو لم تسجلها
• جنى: 4:00 م — دواء بعد الغداء

📿 التسبيح
/tasbeeh 300 حوقلة
هدف شخصي لك.

/tasbeeh جنى 500 الصلاة على النبي ﷺ
أحمد يحدد هدفًا لجنى؛ جنى فقط تزود عداده.

/tasbeeh سوا 500 الصلاة على النبي ﷺ
هدف مشترك يزوده أحمد وجنى.

/tasbeeh list
عرض الأهداف النشطة.

/tasbeeh reset 2
تصفير الهدف رقم 2.

/tasbeeh stop 2
إيقاف الهدف رقم 2.

🎯 التركيز
/focus 10
يبدأ 10 دقائق. رسائلك في الجروب أثناء الجلسة تتمسح تلقائيًا.

/focus off
إنهاء الجلسة فورًا.

🎧 الصوتيات — في الخاص
Reply على صوت سورة الملك واكتب /audio mulk
Reply على صوت أذكار النوم واكتب /audio sleep

🛠️ إعداد أول مرة
/chatid داخل الجروب — معرفة ID الجروب
/myid — معرفة Telegram ID
"""
    )


async def set_quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await remember_user(update, context):
        return
    text = (update.effective_message.text or "").partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text("اكتبها بهذا الشكل:\n/set_quote الخاطرة هنا")
        return
    db = context.application.bot_data["db"]
    await db.set_quote(today_str(), update.effective_user.id, text)
    await update.effective_message.reply_text("✅ اتسجلت خاطرة اليوم للطرف الآخر.")


async def set_hidden_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or await context.application.bot_data["db"].get_user_role(update.effective_user.id) != "ahmed"
        or update.effective_chat is None
        or update.effective_chat.type != ChatType.PRIVATE
    ):
        return
    text = (update.effective_message.text or "").partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text(
            "اكتبها بهذا الشكل:\n/set_hidden_message الرسالة التي تريد أن تظهر لجنى"
        )
        return
    db = context.application.bot_data["db"]
    await db.set_hidden_message(text)
    await update.effective_message.reply_text("✅ اتخزنت الرسالة المخفية لأحمد → جنى.")


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return

    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    user_id = user.id
    raw = (message.text or "").partition(" ")[2].strip()
    db = context.application.bot_data["db"]
    role = await db.get_user_role(user_id)
    name = ROLE_NAMES.get(role, "المستخدم")

    # /focus off is intentionally allowed during an active focus session.
    if raw.lower() == "off":
        old = await db.get_focus(user_id)
        await db.clear_focus(user_id)
        if old and old["notice_chat_id"] and old["notice_message_id"]:
            with_context = context
            try:
                await with_context.bot.edit_message_text(
                    chat_id=old["notice_chat_id"],
                    message_id=old["notice_message_id"],
                    text=f"✅ {name} أنهى جلسة التركيز يدويًا.",
                )
            except TelegramError:
                pass
        try:
            if update.effective_chat and update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message.message_id)
        except TelegramError:
            pass
        await context.bot.send_message(chat_id=user_id, text="✅ وضع التركيز اتقفل.")
        return

    minutes = FOCUS_DEFAULT_MINUTES
    if raw:
        try:
            minutes = max(1, int(raw))
        except ValueError:
            await message.reply_text("اكتب عدد الدقائق، مثلًا: /focus 10")
            return

    # Replace an existing session cleanly.
    old = await db.get_focus(user_id)
    if old and old["notice_chat_id"] and old["notice_message_id"]:
        try:
            await context.bot.delete_message(
                chat_id=old["notice_chat_id"], message_id=old["notice_message_id"]
            )
        except TelegramError:
            pass
    await db.clear_focus(user_id)

    until = local_now() + timedelta(minutes=minutes)
    group_notice = await context.bot.send_message(
        chat_id=CONFIG.group_chat_id,
        text=(
            f"🎯 {name} في جلسة تركيز الآن.\n"
            f"⏱️ الجلسة تنتهي حوالي {focus_until_text(until)}.\n"
            "🚫 أي رسالة يرسلها أثناء الجلسة هتتمسح تلقائيًا من الجروب.\n"
            "خلّوا الجروب هادي لحد ما يخلص 🤍"
        ),
    )
    await db.set_focus(
        user_id,
        until.isoformat(),
        CONFIG.group_chat_id,
        group_notice.message_id,
    )
    try:
        await context.bot.pin_chat_message(
            chat_id=CONFIG.group_chat_id,
            message_id=group_notice.message_id,
            disable_notification=True,
        )
    except TelegramError:
        pass

    # Delete the /focus command itself when started from the group.
    if update.effective_chat and update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id, message_id=message.message_id
            )
        except TelegramError:
            pass

    pm_message = await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🎯 Focus Mode بدأ.\n"
            f"المدة: {minutes} دقيقة\n"
            f"ينتهي حوالي: {focus_until_text(until)}\n"
            "أي رسالة هتبعتها في الجروب أثناء الجلسة هتتمسح تلقائيًا. ركز 🤍"
        ),
    )
    try:
        await context.bot.pin_chat_message(
            chat_id=user_id, message_id=pm_message.message_id, disable_notification=True
        )
    except TelegramError:
        pass

    if context.job_queue:
        context.job_queue.run_once(
            focus_finished_job,
            when=until,
            data={"user_id": user_id, "focus_until": until.isoformat(), "name": name},
            name=f"focus_end_{user_id}",
            user_id=user_id,
            job_kwargs={"replace_existing": True},
        )


async def focus_message_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete all group messages written by a user while their Focus session is active."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    db = context.application.bot_data["db"]
    focus = await db.get_focus(user.id)
    if not focus:
        return

    try:
        until = datetime.fromisoformat(focus["focus_until"])
    except ValueError:
        await db.clear_focus(user.id)
        return

    if until <= local_now():
        await db.clear_focus(user.id)
        return

    # The only command allowed through during Focus is /focus off.
    text = (message.text or message.caption or "").strip().lower()
    if text.startswith("/focus") and text.split(maxsplit=1)[-1:] == ["off"]:
        return

    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)
    except TelegramError as exc:
        context.application.logger.warning(
            "Could not delete Focus message %s from %s: %s", message.message_id, user.id, exc
        )

    # Keep one persistent notice in the group instead of creating a new notice for every deleted message.
    notice_chat_id = focus["notice_chat_id"] or chat.id
    notice_message_id = focus["notice_message_id"]
    name = ROLE_NAMES.get(await db.get_user_role(user.id), "المستخدم")
    notice_text = (
        f"🎯 {name} في جلسة تركيز الآن.\n"
        f"⏱️ الجلسة تنتهي حوالي {focus_until_text(until)}.\n"
        "🚫 أي رسالة يرسلها أثناء الجلسة هتتمسح تلقائيًا من الجروب.\n"
        "خلّوا الجروب هادي لحد ما يخلص 🤍"
    )

    if notice_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=notice_chat_id,
                message_id=notice_message_id,
                text=notice_text,
            )
        except TelegramError:
            notice_message_id = None

    if notice_message_id is None:
        notice = await context.bot.send_message(chat_id=chat.id, text=notice_text)
        await db.set_focus(user.id, focus["focus_until"], chat.id, notice.message_id)

    raise ApplicationHandlerStop


async def focus_finished_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    user_id = int(data["user_id"])
    expected = str(data["focus_until"])
    name = str(data.get("name", "المستخدم"))
    db = context.application.bot_data["db"]
    current = await db.get_focus(user_id)
    if not current or current["focus_until"] != expected:
        return

    notice_chat_id = current["notice_chat_id"]
    notice_message_id = current["notice_message_id"]
    await db.clear_focus(user_id)
    finished_text = f"✅ {name} خلصت جلسة التركيز. رجعنا للوضع العادي 🤍"

    if notice_chat_id and notice_message_id:
        try:
            try:
                await context.bot.unpin_chat_message(
                    chat_id=notice_chat_id, message_id=notice_message_id
                )
            except TelegramError:
                pass
            await context.bot.edit_message_text(
                chat_id=notice_chat_id,
                message_id=notice_message_id,
                text=finished_text,
            )
        except TelegramError:
            await context.bot.send_message(chat_id=CONFIG.group_chat_id, text=finished_text)
    else:
        await context.bot.send_message(chat_id=CONFIG.group_chat_id, text=finished_text)
    await context.bot.send_message(chat_id=user_id, text=finished_text)


async def _send_tasbeeh_goal_message(context: ContextTypes.DEFAULT_TYPE, goal) -> None:
    db = context.application.bot_data["db"]
    owner_name = None
    creator_name = None
    if goal["owner_user_id"]:
        owner_name = await current_user_name(context, int(goal["owner_user_id"]))
    if goal["created_by_user_id"]:
        creator_name = await current_user_name(context, int(goal["created_by_user_id"]))
    await context.bot.send_message(
        chat_id=CONFIG.group_chat_id,
        text=tasbeeh_text(goal, owner_name, creator_name),
        reply_markup=tasbeeh_keyboard(int(goal["id"])),
    )


async def tasbeeh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await remember_user(update, context):
        return

    raw = (update.effective_message.text or "").partition(" ")[2].strip()
    db = context.application.bot_data["db"]
    sender_id = update.effective_user.id
    sender_role = await db.get_user_role(sender_id)

    if raw.lower() == "list":
        goals = await db.list_tasbeeh_goals()
        lines = ["📿 أهداف التسبيح الحالية:", ""]
        for goal in goals:
            if goal["scope"] == "shared":
                scope = "🤝 سوا"
            else:
                owner_role = await db.get_user_role(int(goal["owner_user_id"])) if goal["owner_user_id"] else None
                scope = f"👤 {ROLE_NAMES.get(owner_role, 'صاحبه')}"
            lines.append(f"#{goal['id']} — {scope} — {goal['title']} — {goal['count']}/{goal['target']}")
        await update.effective_message.reply_text(
            "\n".join(lines) if goals else "📿 مفيش أهداف تسبيح نشطة حاليًا."
        )
        return

    parts = raw.split(maxsplit=2)
    if not parts:
        await update.effective_message.reply_text(
            "مثال سريع:\n"
            "/tasbeeh 300 حوقلة\n"
            "/tasbeeh جنى 500 الصلاة على النبي ﷺ\n"
            "/tasbeeh سوا 500 الصلاة على النبي ﷺ"
        )
        return

    # reset/stop management
    if parts[0].lower() in {"reset", "stop"}:
        if len(parts) < 2 or not parts[1].isdigit():
            await update.effective_message.reply_text("مثال: /tasbeeh reset 3")
            return
        goal_id = int(parts[1])
        goal = await db.get_tasbeeh_goal(goal_id)
        if not goal:
            await update.effective_message.reply_text("❌ الهدف ده مش موجود.")
            return
        owner_id = int(goal["owner_user_id"]) if goal["owner_user_id"] else None
        creator_id = int(goal["created_by_user_id"]) if goal["created_by_user_id"] else None
        allowed = (
            goal["scope"] == "shared"
            or sender_id in {owner_id, creator_id}
        )
        if not allowed:
            await update.effective_message.reply_text("❌ مش مسموح لك تدير الهدف ده.")
            return
        if parts[0].lower() == "reset":
            changed = await db.reset_tasbeeh_goal(goal_id)
            text = "✅ العداد اتصفر." if changed else "ℹ️ العداد كان متصفر بالفعل."
        else:
            changed = await db.deactivate_tasbeeh_goal(goal_id)
            text = "✅ الهدف اتقفل." if changed else "ℹ️ الهدف مقفول بالفعل."
        await update.effective_message.reply_text(text)
        if changed and parts[0].lower() == "reset":
            goal = await db.get_tasbeeh_goal(goal_id)
            if goal:
                await _send_tasbeeh_goal_message(context, goal)
        return

    def normalize_target(raw_target: str) -> str | None:
        key = raw_target.strip().lower()
        return {
            "جنى": "janna",
            "janna": "janna",
            "أحمد": "ahmed",
            "احمد": "ahmed",
            "ahmed": "ahmed",
            "سوا": "shared",
            "معًا": "shared",
            "مشترك": "shared",
            "shared": "shared",
            "together": "shared",
        }.get(key)

    mode = normalize_target(parts[0])

    if mode == "shared":
        if len(parts) < 2 or not parts[1].isdigit():
            await update.effective_message.reply_text("مثال: /tasbeeh سوا 500 الصلاة على النبي ﷺ")
            return
        target = int(parts[1])
        title = parts[2].strip() if len(parts) == 3 and parts[2].strip() else "الصلاة على النبي ﷺ"
        scope = "shared"
        owner_id = None
    elif mode in {"janna", "ahmed"}:
        if len(parts) < 3 or not parts[1].isdigit():
            await update.effective_message.reply_text(
                "مثال: /tasbeeh جنى 500 الصلاة على النبي ﷺ"
            )
            return
        if mode == "ahmed" and sender_role != "ahmed":
            await update.effective_message.reply_text("❌ أحمد نفسه هو الذي ينشئ أهدافه الشخصية.")
            return
        if mode == "janna" and sender_role == "janna":
            # Janna may create her own goal with /tasbeeh 500 ..., but this explicit
            # form is also accepted for convenience.
            pass
        target = int(parts[1])
        title = parts[2].strip()
        participants = await db.get_participants()
        owner_row = participants.get(mode)
        if not owner_row:
            await update.effective_message.reply_text("❌ الطرف ده لازم يعمل /start مع البوت الأول.")
            return
        owner_id = int(owner_row["user_id"])
        # A Janna-targeted goal can be created by Ahmed or by Janna herself.
        if mode == "janna" and sender_role not in {"ahmed", "janna"}:
            return
        scope = "personal"
    else:
        # Easiest form: target + title = personal goal for the sender.
        if not parts[0].isdigit():
            await update.effective_message.reply_text(
                "استخدم واحد من دول:\n"
                "/tasbeeh 300 حوقلة\n"
                "/tasbeeh جنى 500 الصلاة على النبي ﷺ\n"
                "/tasbeeh سوا 500 الصلاة على النبي ﷺ"
            )
            return
        target = int(parts[0])
        title = parts[1].strip() if len(parts) >= 2 else "الصلاة على النبي ﷺ"
        scope = "personal"
        owner_id = sender_id

    if target <= 0:
        await update.effective_message.reply_text("❌ الهدف لازم يكون أكبر من صفر.")
        return

    goal_id = await db.create_tasbeeh_goal(
        scope, owner_id, title, target, created_by_user_id=sender_id
    )
    goal = await db.get_tasbeeh_goal(goal_id)
    await update.effective_message.reply_text(f"✅ اتعمل هدف التسبيح #{goal_id}: {title} — {target}")
    await _send_tasbeeh_goal_message(context, goal)

    # If Ahmed assigned a personal goal to Janna, tell Janna directly too.
    if scope == "personal" and owner_id and owner_id != sender_id:
        creator_name = ROLE_NAMES.get(sender_role, "المستخدم")
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                f"📿 {creator_name} حدد لك هدف تسبيح:\n"
                f"{title} — {target}\n"
                "الهدف ظهر في الجروب وتقدري تكمليه من أزراره 🤍"
            ),
        )


async def reset_tasbeeh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await remember_user(update, context):
        return
    db = context.application.bot_data["db"]
    sender_id = update.effective_user.id
    raw = (update.effective_message.text or "").partition(" ")[2].strip()
    goals = await db.list_tasbeeh_goals()
    if raw.isdigit():
        goal_id = int(raw)
    elif len(goals) == 1:
        goal_id = int(goals[0]["id"])
    else:
        await update.effective_message.reply_text("اكتب رقم الهدف، مثل: /reset_tasbeeh 3")
        return
    goal = await db.get_tasbeeh_goal(goal_id)
    if not goal:
        await update.effective_message.reply_text("❌ الهدف ده مش موجود.")
        return
    owner_id = int(goal["owner_user_id"]) if goal["owner_user_id"] else None
    creator_id = int(goal["created_by_user_id"]) if goal["created_by_user_id"] else None
    if sender_id not in {owner_id, creator_id}:
        await update.effective_message.reply_text("❌ مش مسموح لك تدير الهدف ده.")
        return
    changed = await db.reset_tasbeeh_goal(goal_id)
    await update.effective_message.reply_text(
        "✅ العداد اتصفر." if changed else "ℹ️ العداد كان متصفر بالفعل."
    )
    if changed:
        await _send_tasbeeh_goal_message(context, await db.get_tasbeeh_goal(goal_id))


async def set_audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not await remember_user(update, context)
        or update.effective_chat is None
        or update.effective_chat.type != ChatType.PRIVATE
    ):
        return
    args = (update.effective_message.text or "").split(maxsplit=1)
    if len(args) != 2 or args[1].strip().lower() not in {"mulk", "sleep"}:
        await update.effective_message.reply_text(
            "بالرد على الرسالة الصوتية اكتب:\n/set_audio mulk\nأو\n/set_audio sleep"
        )
        return
    kind = args[1].strip().lower()
    reply = update.effective_message.reply_to_message
    if not reply or (not reply.audio and not reply.voice):
        await update.effective_message.reply_text("لازم الأمر يكون Reply على Audio أو Voice.")
        return
    is_voice = bool(reply.voice)
    file_id = reply.audio.file_id if reply.audio else reply.voice.file_id
    db = context.application.bot_data["db"]
    await db.set_setting(f"audio_{kind}", file_id)
    await db.set_setting(f"audio_{kind}_type", "voice" if is_voice else "audio")
    await update.effective_message.reply_text(
        f"✅ اتخزن الصوت الخاص بـ {kind}."
    )


async def quran_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    from scheduler import send_quran_reminder
    today_reminder = await context.application.bot_data["db"].get_reminder(today_str(), "quran", "daily")
    if today_reminder:
        await update.effective_message.reply_text("📖 ورد اليوم اتبعت بالفعل في الجروب. استخدم زر (أنهيت الورد) هناك.")
        return
    await update.effective_message.reply_text("📖 بجلب صفحات ورد اليوم من الإنترنت...")
    await send_quran_reminder(context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await remember_user(update, context):
        return
    user_id = update.effective_user.id
    db = context.application.bot_data["db"]
    today = today_str()
    rows = await db.get_today_completions(today, await db.get_participant_ids())
    own = {(row["item_type"], row["item_key"]): row["status"] for row in rows if int(row["user_id"]) == user_id}
    streak = (await db.get_streak())["current_streak"]
    role = await db.get_user_role(user_id)
    goals = await db.list_tasbeeh_goals()
    focus = await db.get_focus_until(user_id)

    def mark(item_type: str, item_key: str) -> str:
        status = own.get((item_type, item_key))
        if status in {"completed", "already_taken"}:
            return "✅"
        if status == "snoozed":
            return "⏰"
        return "⏳"

    quran_plan = await db.get_quran_plan()
    quran_start = int(quran_plan["start_page"])
    quran_label = quran_range_label(quran_page_numbers(quran_start))

    lines = [
        f"📋 حالة {ROLE_NAMES.get(role, 'المستخدم')} اليوم",
        "",
        f"أذكار الصباح: {mark('azkar', 'morning')}",
        f"أذكار المساء: {mark('azkar', 'evening')}",
        f"أذكار النوم: {mark('azkar', 'sleep')}",
        f"الورد ({quran_label}): {mark('quran', 'daily')}",
    ]
    if role == "janna":
        lines.append(f"دواء قبل الغداء: {mark('med', 'janna_pre_lunch')}")
        lines.append(f"دواء بعد الغداء: {mark('med', 'janna_after_lunch')}")
    elif role == "ahmed":
        lines.append(f"دواء قبل الغداء: {mark('med', 'ahmed_pre_lunch')}")
    else:
        lines.append("الأدوية: —")
    lines += ["", f"🔥 الستريك المشترك: {streak} يوم"]

    if goals:
        lines.append("📿 أهداف التسبيح:")
        for goal in goals:
            owner_ok = goal["scope"] == "shared" or int(goal["owner_user_id"]) == user_id
            if goal["scope"] == "shared" or owner_ok:
                lines.append(f"  #{goal['id']} {goal['title']}: {goal['count']}/{goal['target']}")

    if focus:
        try:
            until = datetime.fromisoformat(focus)
            if until > local_now():
                lines.append(f"🎯 Focus حتى {focus_until_text(until)}")
        except ValueError:
            pass
    await update.effective_message.reply_text("\n".join(lines))


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    from scheduler import send_weekly_digest
    await send_weekly_digest(context)
    if update.effective_message:
        await update.effective_message.reply_text("✅ اتبعت التقرير في الجروب.")


async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    if not await ensure_authorized(update, context):
        await query.answer("مش مسموح بالحساب ده.", show_alert=True)
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if not parts:
        return

    kind = parts[0]
    db = context.application.bot_data["db"]
    today = today_str()

    if kind == "azkar" and len(parts) == 4:
        item_key, item_date, action = parts[1], parts[2], parts[3]
        try:
            datetime.fromisoformat(item_date)
        except ValueError:
            return
        if action != "done" or item_key not in ITEM_LABELS:
            return
        if item_date != today:
            await context.bot.send_message(
                chat_id=user.id,
                text="ℹ️ زر الأذكار ده خاص بتذكير يومه ومش بيتسجل ليوم قديم.",
            )
            return
        inserted = await db.mark_completed(item_date, "azkar", item_key, user.id, "completed")
        statuses = await db.get_statuses(item_date, "azkar", item_key, await db.get_participant_ids())
        participants = await db.get_participants()
        streak = int((await db.get_streak())["current_streak"])
        text = azkar_text(item_key, statuses, streak, participants)
        await edit_callback_message(query, text, query.message.reply_markup)

        if inserted:
            await context.bot.send_message(
                chat_id=user.id,
                text=f"✨ {ITEM_LABELS[item_key]} اتسجلت لك يا {await current_user_name(context, user.id)}.",
            )
            await maybe_send_quote(context, user.id, item_date)

            if item_key == "morning" and await db.get_user_role(user.id) == "janna" and item_date == today:
                hidden = await db.get_hidden_message()
                if hidden:
                    try:
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=f"💌 رسالة مخبأة من أحمد\n\n{hidden}",
                        )
                        await db.clear_hidden_message()
                    except TelegramError:
                        # Keep the message stored if Telegram temporarily rejects the send.
                        pass

            new_streak = None if item_date != today else await update_streak_after_completion(context.application, item_date)
            if new_streak is not None:
                participants = await db.get_participants()
                refreshed_statuses = await db.get_statuses(item_date, "azkar", item_key, await db.get_participant_ids())
                await edit_callback_message(query, azkar_text(item_key, refreshed_statuses, int(new_streak), participants), query.message.reply_markup)
                await context.bot.send_message(
                    chat_id=CONFIG.group_chat_id,
                    text=f"🎉 اكتملت أذكار اليوم من الطرفين! الستريك المشترك أصبح {new_streak} يوم 🔥",
                )
        return

    if kind == "med" and len(parts) == 4:
        item_key, item_date, action = parts[1], parts[2], parts[3]
        if item_key not in MEDICATION_LABELS:
            return
        required_role = MEDICATION_ROLE[item_key]
        if await db.get_user_role(user.id) != required_role:
            target_name = ROLE_NAMES[required_role]
            await context.bot.send_message(chat_id=user.id, text=f"ℹ️ التنبيه ده مخصص لـ{target_name} فقط.")
            return

        if action == "snooze":
            if item_date != today:
                await context.bot.send_message(chat_id=user.id, text="ℹ️ زر التأجيل ده خاص بتذكير اليوم الحالي.")
                return
            await db.set_completion_status(item_date, "med", item_key, user.id, "snoozed")
            remind_at = local_now() + timedelta(minutes=SNOOZE_MINUTES)
            await db.upsert_snooze(user.id, item_date, item_key, remind_at.isoformat())
            status = (await db.get_statuses(item_date, "med", item_key, (user.id,))).get(user.id)
            await edit_reminder_by_key(context, item_date, "med", item_key, medication_text(item_key, status), medication_keyboard(item_key, item_date))
            await context.bot.send_message(chat_id=user.id, text=f"⏰ هفكّرك بـ{MEDICATION_LABELS[item_key]} بعد 15 دقيقة.")
            context.job_queue.run_once(
                medication_snooze_job,
                when=timedelta(minutes=SNOOZE_MINUTES),
                data={"user_id": user.id, "item_key": item_key, "item_date": item_date, "remind_at": remind_at.isoformat()},
                name=f"med_snooze_{user.id}_{item_key}_{item_date}",
                user_id=user.id,
                job_kwargs={"replace_existing": True},
            )
            return

        if action in {"take", "already", "take_after_snooze"}:
            if item_date != today:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="ℹ️ زر الدواء ده خاص بتذكير اليوم ومش بيتسجل لجرعة قديمة.",
                )
                return
            status = "already_taken" if action == "already" else "completed"
            changed, _ = await db.set_completion_status(item_date, "med", item_key, user.id, status)
            await db.deactivate_snooze(user.id, item_date, item_key)
            current_status = (await db.get_statuses(item_date, "med", item_key, (user.id,))).get(user.id)
            await edit_reminder_by_key(context, item_date, "med", item_key, medication_text(item_key, current_status), medication_keyboard(item_key, item_date))
            if changed:
                role_name = ROLE_NAMES[MEDICATION_ROLE[item_key]]
                await context.bot.send_message(chat_id=user.id, text=f"💊 اتسجل {MEDICATION_LABELS[item_key]} لك يا {role_name}.")
            return

    if kind == "quran" and len(parts) == 3 and parts[2] == "done":
        item_date = parts[1]
        try:
            datetime.fromisoformat(item_date)
        except ValueError:
            return
        assignment = await db.get_quran_assignment(item_date)
        if not assignment:
            await context.bot.send_message(chat_id=user.id, text="ℹ️ مش لاقي تخطيط الورد للتاريخ ده.")
            return
        current_plan = await db.get_quran_plan()
        if current_plan["assigned_date"] and current_plan["assigned_date"] != item_date:
            await context.bot.send_message(chat_id=user.id, text="ℹ️ ده زر ورد قديم. استخدم زر ورد اليوم.")
            return
        inserted = await db.mark_completed(item_date, "quran", "daily", user.id, "completed")
        statuses = await db.get_statuses(item_date, "quran", "daily", await db.get_participant_ids())
        participants = await db.get_participants()
        pages = quran_page_numbers(int(assignment["start_page"]))
        text = quran_text(statuses, participants, pages, bool(assignment["carried_over"]))
        await edit_reminder_by_key(context, item_date, "quran", "daily", text, query.message.reply_markup)
        if inserted:
            await context.bot.send_message(chat_id=user.id, text=f"📖 عاش! ورد الصفحات {quran_range_label(pages)} اتسجل لك.")
            other_id = await other_user_id(context, user.id)
            if other_id is not None:
                await send_pm(
                    context,
                    other_id,
                    f"📖 {await current_user_name(context, user.id)} أنهى ورد الصفحات {quran_range_label(pages)} ✨",
                    respect_focus=False,
                )
            if await db.day_joint_quran_complete(item_date, await db.get_participant_ids()):
                await context.bot.send_message(
                    chat_id=CONFIG.group_chat_id,
                    text=f"🎉 خلصتوا ورد الصفحات {quran_range_label(pages)} معًا!\nورد بكرة هينتقل للصفحات التالية تلقائيًا بإذن الله 🤍",
                )
        return

    if kind == "tas" and len(parts) == 3:
        try:
            goal_id, amount = int(parts[1]), int(parts[2])
        except ValueError:
            return
        if amount not in {10, 50}:
            return
        goal = await db.get_tasbeeh_goal(goal_id)
        if not goal or not goal["active"]:
            await context.bot.send_message(chat_id=user.id, text="ℹ️ الهدف ده مش نشط.")
            return
        if goal["scope"] == "personal" and int(goal["owner_user_id"]) != user.id:
            await context.bot.send_message(chat_id=user.id, text="ℹ️ ده هدف شخصي للطرف الآخر.")
            return
        try:
            goal, previous_count = await db.add_tasbeeh(goal_id, amount)
        except ValueError:
            await context.bot.send_message(chat_id=user.id, text="ℹ️ الهدف ده مش متاح حاليًا.")
            return
        owner_name = None
        if goal["scope"] == "personal":
            owner_name = await current_user_name(context, int(goal["owner_user_id"]))
        await edit_callback_message(query, tasbeeh_text(goal, owner_name), tasbeeh_keyboard(goal_id))
        await context.bot.send_message(chat_id=user.id, text=f"📿 زودت {amount}. وصلنا {goal['count']}/{goal['target']}.")
        if previous_count < int(goal["target"]) <= int(goal["count"]):
            scope_label = "المشترك" if goal["scope"] == "shared" else "الشخصي"
            await context.bot.send_message(
                chat_id=CONFIG.group_chat_id,
                text=f"🎉 اكتمل هدف التسبيح {scope_label} #{goal['id']}: {goal['title']} — {goal['target']}! 🤍",
            )
        return

    if kind == "audio" and len(parts) == 2:
        await send_audio(context, user.id, parts[1])
        return


async def maybe_send_quote(context: ContextTypes.DEFAULT_TYPE, completing_user_id: int, item_date: str) -> None:
    db = context.application.bot_data["db"]
    quote = await db.get_quote(item_date)
    if not quote:
        return
    author_id = int(quote["author_id"])
    if author_id == completing_user_id:
        return
    if not await db.claim_quote_delivery(item_date, author_id, completing_user_id):
        return
    participants = await db.get_participants()
    author_name = user_name(author_id, participants)
    await send_pm(
        context,
        completing_user_id,
        f"💭 خاطرة اليوم من {author_name}:\n\n{quote['text']}",
        respect_focus=False,
    )


async def send_audio(context: ContextTypes.DEFAULT_TYPE, user_id: int, kind: str) -> None:
    if kind not in {"mulk", "sleep"}:
        return
    db = context.application.bot_data["db"]
    file_id = await db.get_setting(f"audio_{kind}")
    media_type = await db.get_setting(f"audio_{kind}_type") or "audio"
    if not file_id:
        file_id = CONFIG.mulk_audio_file_id if kind == "mulk" else CONFIG.sleep_audio_file_id
        media_type = "audio"
    if not file_id:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ مفيش صوت محفوظ لسه. في الخاص اعمل Reply على الملف الصوتي واكتب:\n/set_audio mulk\nأو\n/set_audio sleep",
        )
        return
    try:
        if media_type == "voice":
            await context.bot.send_voice(chat_id=user_id, voice=file_id)
        else:
            await context.bot.send_audio(chat_id=user_id, audio=file_id)
    except TelegramError:
        await context.bot.send_message(chat_id=user_id, text="⚠️ حصل خطأ في تشغيل الملف الصوتي.")


async def edit_callback_message(query, text: str, reply_markup) -> None:
    if not query.message:
        return
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text=text, reply_markup=reply_markup)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise


async def edit_reminder_by_key(
    context: ContextTypes.DEFAULT_TYPE,
    item_date: str,
    item_type: str,
    item_key: str,
    text: str,
    reply_markup,
) -> None:
    db = context.application.bot_data["db"]
    row = await db.get_reminder(item_date, item_type, item_key)
    if not row:
        return
    try:
        if row["message_kind"] == "photo":
            await context.bot.edit_message_caption(
                chat_id=row["chat_id"], message_id=row["message_id"], caption=text, reply_markup=reply_markup
            )
        else:
            await context.bot.edit_message_text(
                chat_id=row["chat_id"], message_id=row["message_id"], text=text, reply_markup=reply_markup
            )
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise

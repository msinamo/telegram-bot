# bot_multi_admin_persistent.py
import os
import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, ChatJoinRequestHandler, ChatMemberHandler,
    ContextTypes, CommandHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== تنظیمات از متغیرهای محیطی =====
BOT_TOKEN   = os.getenv("BOT_TOKEN")  or "PUT_BOT_TOKEN_HERE"
CHANNEL_ID  = int(os.getenv("CHANNEL_ID") or -1001234567890)
ADMIN_IDS   = [int(x.strip()) for x in (os.getenv("ADMIN_IDS") or "123456789,987654321").split(",")]

def parse_admin_map(s: Optional[str]) -> Dict[int, str]:
    """ADMIN_MAP شبیه '111:Ali,222:Sara' را به نگاشت {111:'Ali', 222:'Sara'} تبدیل می‌کند"""
    if not s:
        return {}
    out: Dict[int, str] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            aid_str, name = part.split(":", 1)
            out[int(aid_str.strip())] = name.strip()
        except ValueError:
            pass
    return out

ADMIN_MAP: Dict[int, str] = parse_admin_map(os.getenv("ADMIN_MAP"))
DB_PATH = os.getenv("DB_PATH") or "bot_data.sqlite3"

# ===== اسکیما و توابع دیتابیس =====
CREATE_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS join_requests (
    user_id            INTEGER PRIMARY KEY,
    user_payload_json  TEXT NOT NULL,
    status             TEXT NOT NULL,   -- pending | approved | declined
    decided_by         INTEGER,
    decided_at         TEXT
);
CREATE TABLE IF NOT EXISTS admin_messages (
    request_user_id    INTEGER NOT NULL,
    admin_chat_id      INTEGER NOT NULL,
    message_id         INTEGER NOT NULL,
    PRIMARY KEY (request_user_id, admin_chat_id)
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in CREATE_SCHEMA_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s)
        await db.commit()

async def db_save_join_request(user_dict: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO join_requests(user_id, user_payload_json, status, decided_by, decided_at) "
            "VALUES (?, ?, COALESCE((SELECT status FROM join_requests WHERE user_id=?),'pending'), "
            "COALESCE((SELECT decided_by FROM join_requests WHERE user_id=?), NULL), "
            "COALESCE((SELECT decided_at FROM join_requests WHERE user_id=?), NULL))",
            (user_dict["id"], json.dumps(user_dict), user_dict["id"], user_dict["id"], user_dict["id"])
        )
        await db.commit()

async def db_save_admin_message(request_user_id: int, admin_chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO admin_messages(request_user_id, admin_chat_id, message_id) VALUES (?, ?, ?)",
            (request_user_id, admin_chat_id, message_id)
        )
        await db.commit()

async def db_get_admin_messages(request_user_id: int) -> List[Dict[str, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT admin_chat_id, message_id FROM admin_messages WHERE request_user_id = ?",
            (request_user_id,)
        )
        rows = await cur.fetchall()
    return [{"chat_id": r[0], "message_id": r[1]} for r in rows]

async def db_set_decision(user_id: int, status: str, decided_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE join_requests SET status = ?, decided_by = ?, decided_at = ? WHERE user_id = ?",
            (status, decided_by, datetime.now(timezone.utc).isoformat(), user_id)
        )
        await db.commit()

async def db_get_request_status(user_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM join_requests WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None

async def db_clear_request_messages(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_messages WHERE request_user_id = ?", (user_id,))
        await db.commit()

# ===== کمک‌ها =====
def format_user_line(user) -> str:
    uname = f"@{getattr(user, 'username', None)}" if getattr(user, "username", None) else "—"
    full = getattr(user, "full_name", None) or (f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip() or "—")
    return f"👤 نام: {full}\n🆔 آیدی عددی: {user.id}\n📨 یوزرنیم: {uname}"

def admin_display_name(admin_id: int, fallback: Optional[str] = None) -> str:
    if admin_id in ADMIN_MAP:
        return ADMIN_MAP[admin_id]
    return fallback or f"Admin {admin_id}"

# ===== دستورات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = []
    for aid in ADMIN_IDS:
        try:
            chat = await context.bot.get_chat(aid)
            names.append(f"{aid} → {ADMIN_MAP.get(aid, chat.full_name)}")
        except Exception:
            names.append(f"{aid} → {ADMIN_MAP.get(aid, f'Admin {aid}')}")
    txt = "بات فعاله ✅\n\nادمین‌ها:\n" + "\n".join(f"• {n}" for n in names)
    await update.message.reply_text(txt)

async def on_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for aid in ADMIN_IDS:
        display = ADMIN_MAP.get(aid)
        if not display:
            try:
                chat = await context.bot.get_chat(aid)
                display = chat.full_name
            except Exception:
                display = f"Admin {aid}"
        lines.append(f"• {display} ({aid})")
    await update.message.reply_text("ادمین‌های ثبت‌شده:\n" + "\n".join(lines))

async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start → نمایش وضعیت و لیست ادمین‌ها\n"
        "/admins → نمایش لیست ادمین‌ها با نام\n"
        "رویدادها: درخواست عضویت با دکمه‌های تأیید/رد، اعلان عضو شدن/ترک کردن."
    )

# ===== رخداد: درخواست عضویت =====
async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user = req.from_user

    # ذخیره درخواست (pending)
    user_dict = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name
    }
    await db_save_join_request(user_dict)

    text = (
        "📥 درخواست عضویت جدید برای کانال\n"
        f"{format_user_line(user)}\n\n"
        "تصمیم شما؟"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve:{user.id}"),
            InlineKeyboardButton("⛔ رد",     callback_data=f"decline:{user.id}")
        ]
    ])

    # ارسال به همه ادمین‌ها + ذخیره message_id ها
    for admin_id in ADMIN_IDS:
        try:
            msg = await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            await db_save_admin_message(user.id, admin_id, msg.message_id)
        except Exception as e:
            logger.warning(f"failed sending join request to {admin_id}: {e}")

# ===== رخداد: کلیک دکمه‌ها =====
async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor_id = query.from_user.id

    # فقط ادمین‌های مجاز
    if actor_id not in ADMIN_IDS:
        await query.answer("شما ادمین این ربات نیستید.", show_alert=True)
        return

    await query.answer()

    try:
        action, uid_str = query.data.split(":")
        user_id = int(uid_str)
    except Exception:
        await query.edit_message_text("⚠️ داده نامعتبر.")
        return

    # اگر قبلاً تصمیم گرفته شده بود
    prev_status = await db_get_request_status(user_id)
    if prev_status in ("approved", "declined"):
        decided_by_name = admin_display_name(actor_id, getattr(query.from_user, "full_name", None))
        result_text = "✅ از قبل تأیید شده بود." if prev_status == "approved" else "⛔ از قبل رد شده بود."
        await _update_all_admin_msgs(context, user_id, f"{result_text}\n(اقدام‌کننده: {decided_by_name})")
        return

    decided_by_name = admin_display_name(actor_id, getattr(query.from_user, "full_name", None))
    try:
        if action == "approve":
            await context.bot.approve_chat_join_request(chat_id=CHANNEL_ID, user_id=user_id)
            await db_set_decision(user_id, "approved", actor_id)
            result_text = f"✅ درخواست تأیید شد.\n(اقدام‌کننده: {decided_by_name})"
        elif action == "decline":
            await context.bot.decline_chat_join_request(chat_id=CHANNEL_ID, user_id=user_id)
            await db_set_decision(user_id, "declined", actor_id)
            result_text = f"⛔ درخواست رد شد.\n(اقدام‌کننده: {decided_by_name})"
        else:
            await query.edit_message_text("⚠️ اقدام ناشناخته.")
            return
    except Exception as e:
        await query.edit_message_text(f"⚠️ خطا: {e}")
        return

    await _update_all_admin_msgs(context, user_id, result_text)

async def _update_all_admin_msgs(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    entries = await db_get_admin_messages(user_id)
    if not entries:
        return
    for entry in entries:
        try:
            await context.bot.edit_message_text(
                chat_id=entry["chat_id"],
                message_id=entry["message_id"],
                text=text
            )
        except Exception as e:
            logger.debug(f"edit failed for admin {entry['chat_id']}: {e}")
    await db_clear_request_messages(user_id)

# ===== رخداد: عضو شد / ترک کرد =====
async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if cmu.chat.id != CHANNEL_ID:
        return

    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    member = cmu.new_chat_member.user

    joined = (new_status in ("member", "administrator")) and (old_status not in ("member", "administrator"))
    left   = new_status in ("left", "kicked")

    if joined:
        msg = f"✅ عضو شد:\n{format_user_line(member)}"
        await _notify_admins(context, msg)
    elif left:
        msg = f"🚪 ترک کرد:\n{format_user_line(member)}"
        await _notify_admins(context, msg)

async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning(f"notify admin {admin_id} failed: {e}")

# ===== ساخت و اجرا =====
async def on_help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await on_help(update, context)

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admins", on_admins))
    app.add_handler(CommandHandler("help", on_help_cmd))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=r"^(approve|decline):\d+$"))
    app.add_handler(ChatMemberHandler(on_chat_member))
    return app

async def preflight(app: Application):
    await db_init()
    try:
        await app.bot.get_chat(CHANNEL_ID)  # تست دسترسی به کانال (اختیاری)
    except Exception as e:
        logger.warning(f"Channel access check failed: {e}")

def main():
    app = build_app()
    app.post_init(preflight)
    app.run_polling(
        allowed_updates=["chat_join_request", "chat_member", "callback_query", "message"]
    )

if __name__ == "__main__":
    main()

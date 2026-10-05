import os
import re
import asyncio
import logging
from functools import wraps
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from database import init_db, add_user, get_all_users, get_user_count
from music import (
    search_youtube,
    download_audio,
    get_video_info,
    cleanup_file,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN       = os.getenv("BOT_TOKEN")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@aaaaaallllllllliiiii")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN .env faylida topilmadi!")

# Regex for any URL
URL_PATTERN = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE,
)

# ─── Subscription ─────────────────────────────────────────────────────────────

async def check_subscription(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in (
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        )
    except TelegramError as e:
        err = str(e).lower()
        logger.warning(f"Sub check error (uid={user_id}): {e}")
        # Bot not in channel → can't verify → allow
        if any(k in err for k in ("chat not found", "bot is not a member", "not enough rights")):
            return True
        # "user not found" / "participant_id_invalid" → definitely not subscribed
        if any(k in err for k in ("user not found", "participant_id_invalid")):
            return False
        return True   # unknown error → allow to avoid false blocks


def sub_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 Kanalga obuna bo'lish",
            url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
        )],
        [InlineKeyboardButton("✅ Obuna bo'ldim, tekshir", callback_data="check_sub")],
    ])


async def send_sub_prompt(target, is_callback: bool = False):
    text = (
        f"⚠️ *Botdan foydalanish uchun kanalga obuna bo'lishingiz kerak!*\n\n"
        f"📢 Kanal: {CHANNEL_USERNAME}\n\n"
        "1️⃣ Yuqoridagi tugmani bosib kanalga o'ting\n"
        "2️⃣ Obuna bo'lgach ✅ tugmasini bosing 👇"
    )
    kb = sub_keyboard()
    if is_callback:
        await target.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != ADMIN_ID:
            await update.effective_message.reply_text("❌ Siz admin emassiz!")
            return
        return await func(update, context)
    return wrapper


def fmt_dur(sec) -> str:
    if not sec:
        return "?"
    try:
        m, s = divmod(int(sec), 60)
        return f"{m}:{s:02d}"
    except Exception:
        return "?"


# ─── Core: send a downloaded file to user ─────────────────────────────────────

async def send_audio_file(
    file_path: str,
    chat_id: int,
    bot,
    title: str = "",
    performer: str = "",
    caption: str = "",
) -> bool:
    """
    Send audio file. Returns True on success.
    Renames file temporarily to ensure clean filename if sent as document.
    """
    MAX_SIZE = 50 * 1024 * 1024
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return False

    if size == 0:
        logger.error("Downloaded file is 0 bytes")
        return False

    if size > MAX_SIZE:
        logger.warning(f"File too large: {size} bytes")
        return False

    # Rename file for a clean name in Telegram
    ext = os.path.splitext(file_path)[1]
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    safe_title = safe_title[:50] or "Audio"
    clean_path = os.path.join(os.path.dirname(file_path), f"{safe_title}{ext}")
    
    try:
        os.rename(file_path, clean_path)
        file_to_send = clean_path
    except Exception as e:
        logger.error(f"Rename failed: {e}")
        file_to_send = file_path

    success = False
    with open(file_to_send, "rb") as f:
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=f,
                title=title or None,
                performer=performer or None,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                read_timeout=60,
                write_timeout=60,
            )
            success = True
        except TelegramError as e:
            logger.warning(f"send_audio failed: {e} — trying send_document")
            f.seek(0)
            try:
                await bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    read_timeout=60,
                    write_timeout=60,
                )
                success = True
            except TelegramError as e2:
                logger.error(f"send_document also failed: {e2}")

    # Clean up the renamed file
    if file_to_send != file_path:
        cleanup_file(file_to_send)
        
    return success


# ─── Core: search and show results ────────────────────────────────────────────

async def show_search_results(query_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search YouTube and send inline button results."""
    chat_id = update.effective_chat.id

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔍 *{query_text}* qidirilmoqda...\n⏳ Iltimos kuting...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, search_youtube, query_text, 8
        )
    except Exception as e:
        logger.error(f"Search executor error: {e}")
        results = []

    if not results:
        await status.edit_text(
            f"😔 *{query_text}* bo'yicha hech narsa topilmadi.\n\n"
            "🔄 Boshqa nom bilan urinib ko'ring.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    keyboard = []
    for i, r in enumerate(results, start=1):
        t = r["title"]
        short = (t[:42] + "…") if len(t) > 42 else t
        keyboard.append([
            InlineKeyboardButton(
                f"🎵 {i}. {short} [{fmt_dur(r.get('duration'))}]",
                callback_data=f"dl_{r['id']}",
            )
        ])

    await status.edit_text(
        f"🎵 *'{query_text}'* bo'yicha {len(results)} ta natija:\n\n"
        "👇 Yuklab olish uchun qo'shiqni tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── Core: download by video_id or url and send ───────────────────────────────

async def download_and_send(
    video_url: str,
    video_id: str,
    title: str,
    channel: str,
    user_id: int,
    chat_id: int,
    status_msg,       # message to edit with progress
    bot,
):
    """Download audio and send to user. Edits status_msg with progress."""
    await status_msg.edit_text(
        "⬇️ *Qo'shiq yuklanmoqda...*\n"
        "⏳ Bir necha soniya kuting...",
        parse_mode=ParseMode.MARKDOWN,
    )

    output_filename = f"song_{user_id}_{video_id}"
    file_path = None
    try:
        file_path = await asyncio.get_event_loop().run_in_executor(
            None, download_audio, video_url, output_filename
        )
    except Exception as e:
        logger.error(f"Download executor error: {e}")

    if not file_path:
        await status_msg.edit_text(
            "❌ *Bu qo'shiqni yuklab bo'lmadi.*\n\n"
            "🔄 Boshqa natijani tanlang yoki boshqa qo'shiq nomini yozing.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Check size
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = 0

    MAX_SIZE = 50 * 1024 * 1024
    if size == 0:
        cleanup_file(file_path)
        await status_msg.edit_text(
            "❌ *Yuklangan fayl bo'sh chiqdi.*\n\n"
            "🔄 Boshqa natijani tanlang.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if size > MAX_SIZE:
        cleanup_file(file_path)
        await status_msg.edit_text(
            "⚠️ *Qo'shiq fayli juda katta (50 MB dan oshadi).*\n\n"
            "🔄 Boshqa natijani tanlang.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await status_msg.edit_text("📤 *Yuborilmoqda...*", parse_mode=ParseMode.MARKDOWN)

    # Parse performer and title
    performer = channel
    clean_title = title
    if "-" in title:
        parts = title.split("-", 1)
        performer = parts[0].strip()
        clean_title = parts[1].strip()

    caption = "🎵 *O'zbek Qo'shiqlar Bot*\n\n🔍 Boshqa qo'shiq nomini yozing!"
    success = await send_audio_file(
        file_path=file_path,
        chat_id=chat_id,
        bot=bot,
        title=clean_title,
        performer=performer,
        caption=caption,
    )
    cleanup_file(file_path)

    if success:
        try:
            await status_msg.delete()
        except Exception:
            pass
    else:
        await status_msg.edit_text(
            "❌ *Faylni yuborishda xatolik yuz berdi.*\n\n"
            "🔄 Boshqa qo'shiqni sinab ko'ring.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    if not await check_subscription(context.bot, user.id):
        context.user_data["pending_query"] = None
        await send_sub_prompt(update.message)
        return

    await update.message.reply_text(
        f"👋 Salom, *{user.first_name}*!\n\n"
        "🎵 *O'zbek Qo'shiqlar Botiga* xush kelibsiz!\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔍 *Qidiruv usullari:*\n"
        "• Qo'shiq nomini yozing\n"
        "• San'atkor ismini yozing\n"
        "• YouTube/video havolasini yuboring\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📝 *Misollar:*\n"
        "`Shaxriyar`, `Ulug'bek Rahmatullayev`\n"
        "`https://youtube.com/watch?v=...`\n\n"
        "🎵 Qo'shiq nomini kiriting ⬇️",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 *O'zbek Qo'shiqlar Bot — Yordam*\n\n"
        "📖 *Qanday ishlatiladi:*\n"
        "• Qo'shiq nomini yozing → 8 ta natija chiqadi\n"
        "• San'atkor ismini yozing → barcha qo'shiqlari\n"
        "• YouTube/TikTok havolasini yuboring → to'g'ridan yuklab oladi\n\n"
        "📝 *Misollar:*\n"
        "`Shaxriyar yig'lasam`\n"
        "`Ulug'bek Rahmatullayev`\n"
        "`https://www.youtube.com/watch?v=XXXXXXX`\n\n"
        "⚙️ *Buyruqlar:*\n"
        "/start — Botni qayta boshlash\n"
        "/help — Yordam\n"
        "/admin — Admin panel (faqat admin)",
        parse_mode=ParseMode.MARKDOWN,
    )


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all text messages."""
    user = update.effective_user
    text = update.message.text.strip()

    if text.startswith("/"):
        return

    # Admin broadcast mode
    if user.id == ADMIN_ID and context.user_data.get("awaiting_broadcast"):
        await handle_broadcast_text(update, context)
        return

    # Save user to DB
    await add_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    # Check subscription
    if not await check_subscription(context.bot, user.id):
        context.user_data["pending_query"] = text
        await send_sub_prompt(update.message)
        return

    # ── URL detected → direct download ────────────────────────────────────────
    url_match = URL_PATTERN.search(text)
    if url_match:
        await handle_direct_url(url_match.group(0), update, context)
        return

    # ── Text query → search ────────────────────────────────────────────────────
    await show_search_results(text, update, context)


async def handle_direct_url(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download from a direct URL (YouTube, TikTok, etc.)."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    status = await update.message.reply_text(
        f"🔗 *Havola topildi!*\n\n"
        "⏳ Ma'lumotlar olinmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Get video title first
    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, get_video_info, url
        )
    except Exception as e:
        logger.error(f"get_video_info error: {e}")
        info = None

    if not info:
        await status.edit_text(
            "❌ *Bu havoladan ma'lumot olishda xatolik.*\n\n"
            "Havola to'g'riligini tekshiring yoki boshqa havola yuboring.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    title = info["title"]
    short_title = (title[:50] + "…") if len(title) > 50 else title

    await status.edit_text(
        f"🎵 *{short_title}*\n"
        f"⏱ Davomiyligi: {fmt_dur(info.get('duration'))}\n\n"
        "⬇️ Yuklanmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    await download_and_send(
        video_url=url,
        video_id=f"url_{hash(url) % 100000}",
        title=title,
        channel=info.get("channel", "O'zbek Qo'shiqlar"),
        user_id=user.id,
        chat_id=chat_id,
        status_msg=status,
        bot=context.bot,
    )


# ─── Callback: subscription check ─────────────────────────────────────────────

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer("⏳ Tekshirilmoqda...")

    if not await check_subscription(context.bot, user.id):
        await query.edit_message_text(
            "❌ *Siz hali kanalga obuna bo'lmadingiz!*\n\n"
            f"📢 {CHANNEL_USERNAME} kanaliga obuna bo'ling,\n"
            "keyin ✅ tugmasini bosing:",
            reply_markup=sub_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Subscribed! ──
    try:
        await query.delete_message()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"✅ *Rahmat, {user.first_name}!* Obuna tasdiqlandi.\n\n"
            "🎵 Qo'shiq nomi yoki havolani yuboring!"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    # Auto-run pending query if user had typed something before subscribing
    pending = context.user_data.pop("pending_query", None)
    if pending:
        url_match = URL_PATTERN.search(pending)
        if url_match:
            await handle_direct_url(url_match.group(0), update, context)
        else:
            await show_search_results(pending, update, context)


# ─── Callback: download by video_id ───────────────────────────────────────────

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = update.effective_user
    await query.answer("⬇️ Yuklanmoqda...")

    if not await check_subscription(context.bot, user.id):
        await query.edit_message_text(
            "⚠️ Botdan foydalanish uchun kanalga obuna bo'ling!",
            reply_markup=sub_keyboard(),
        )
        return

    video_id  = query.data.replace("dl_", "")
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # Fetch real info so we can set title and performer correctly
    info = None
    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, get_video_info, video_url
        )
    except Exception as e:
        logger.error(f"get_video_info error in download_callback: {e}")

    title = info["title"] if info else f"Audio_{video_id}"
    channel = info["channel"] if info else "O'zbek Qo'shiqlar"

    await download_and_send(
        video_url=video_url,
        video_id=video_id,
        title=title,
        channel=channel,
        user_id=user.id,
        chat_id=update.effective_chat.id,
        status_msg=query.message,
        bot=context.bot,
    )


# ─── Admin panel ──────────────────────────────────────────────────────────────

@admin_only
async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 *Admin Panel*\n\nAmallardan birini tanlang:",
        reply_markup=admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Foydalanuvchilar soni", callback_data="adm_count")],
        [InlineKeyboardButton("📋 Barcha foydalanuvchilar", callback_data="adm_list")],
        [InlineKeyboardButton("📢 Hammaga xabar yuborish", callback_data="adm_broadcast")],
    ])


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = update.effective_user

    if user.id != ADMIN_ID:
        await query.answer("❌ Siz admin emassiz!", show_alert=True)
        return

    await query.answer()
    data = query.data
    back = [[InlineKeyboardButton("🔙 Orqaga", callback_data="adm_back")]]

    if data == "adm_count":
        count = await get_user_count()
        await query.edit_message_text(
            f"👥 *Foydalanuvchilar soni:*\n\n✅ Jami: *{count}* ta",
            reply_markup=InlineKeyboardMarkup(back),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm_list":
        users = await get_all_users()
        count = await get_user_count()
        if not users:
            await query.edit_message_text(
                "😔 Hali hech qanday foydalanuvchi yo'q.",
                reply_markup=InlineKeyboardMarkup(back),
            )
            return
        lines = [f"👥 *Barcha foydalanuvchilar ({count} ta):*\n"]
        for uid, uname, fname, lname, joined in users[:30]:
            uname_str = f"@{uname}" if uname else "—"
            full = f"{fname or ''} {lname or ''}".strip() or "Nomsiz"
            date = str(joined)[:10] if joined else "?"
            lines.append(f"• [{full}](tg://user?id={uid}) | {uname_str} | 📅 {date}")
        if count > 30:
            lines.append(f"\n_...va yana {count - 30} ta_")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n_...kesildi_"
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(back),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "📢 *Broadcast xabar*\n\n"
            "Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yozing:\n"
            "_(Bekor qilish: /cancel)_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm_back":
        await query.edit_message_text(
            "🔧 *Admin Panel*\n\nAmallardan birini tanlang:",
            reply_markup=admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "/cancel":
        context.user_data["awaiting_broadcast"] = False
        await update.message.reply_text("❌ Broadcast bekor qilindi.")
        return

    context.user_data["awaiting_broadcast"] = False
    msg_text = update.message.text
    users = await get_all_users()
    sent = failed = 0

    status = await update.message.reply_text(f"📢 Yuborilmoqda... 0/{len(users)}")

    for idx, (uid, *_) in enumerate(users, start=1):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 *Admin xabari:*\n\n{msg_text}",
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except TelegramError:
            failed += 1
        if idx % 20 == 0:
            try:
                await status.edit_text(f"📢 Yuborilmoqda... {idx}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"✅ *Broadcast yakunlandi!*\n\n"
        f"📤 Yuborildi: *{sent}* ta\n"
        f"❌ Xatolik: *{failed}* ta",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── App ──────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    await init_db()
    logger.info(f"✅ Bot started | Admin={ADMIN_ID} | Channel={CHANNEL_USERNAME}")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(60)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help",  help_handler))
    app.add_handler(CommandHandler("admin", admin_handler))

    # Callbacks — specific first
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(download_callback,  pattern=r"^dl_"))
    app.add_handler(CallbackQueryHandler(admin_callback,     pattern=r"^adm_"))

    # All text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    logger.info("🚀 Polling started…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

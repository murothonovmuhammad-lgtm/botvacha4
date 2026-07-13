import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web

# --- KONFIGURATSIYA ---
BOT_TOKEN = "8855828283:AAHBhbNOkgq6AWVnGp4r4XP6Nmo3fPco9O0"
ADMIN_ID = 8809803548  # O'zingizning Telegram ID'ngizni yozing

REQUIRED_CHANNEL = "-1003933617682"
CHANNEL_URL = "https://t.me/+VkY-QD49KghhMzli"

# --- BOT VA DISPATCHER ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- MA'LUMOTLAR OMBORI (SQLITE) ---
conn = sqlite3.connect("kinobot.db", check_same_thread=False)
cursor = conn.cursor()

# Jadvallarni yaratish va yangilash
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    daily_count INTEGER DEFAULT 0,
    last_request_date TEXT,
    vip_until TEXT,
    last_seen TEXT,
    gift_received INTEGER DEFAULT 0
)""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS movies (
    code TEXT PRIMARY KEY,
    file_id TEXT,
    name TEXT,
    country TEXT,
    lang TEXT,
    resolution TEXT
)""")

# Sozlamalar jadvali
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)""")

# Karta raqami default qiymati agar yo'q bo'lsa
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_number', 'Karta raqami kiritilmagan')")
conn.commit()

# Eski bazalarda ustunlar bo'lmasa, ularni qo'shish
try:
    cursor.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE users ADD COLUMN gift_received INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

# --- FSM (STATES) ---
class MovieUpload(StatesGroup):
    name = State()
    code = State()
    country = State()
    lang = State()
    resolution = State()
    video = State()

class AdminAd(StatesGroup):
    waiting_for_ad = State()

# --- YORDAMCHI FUNKSIYALAR ---
def check_vip(user_id):
    cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res and res[0]:
        until = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        if until > datetime.now():
            return True
    return False

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        print(f"[is_subscribed xatolik] user_id={user_id}, xato={e}")
        return False

def touch_user(user_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now_str, user_id))
    else:
        cursor.execute("INSERT INTO users (user_id, last_seen) VALUES (?, ?)", (user_id, now_str))
    conn.commit()

def check_limit_and_update(user_id):
    touch_user(user_id)
    if check_vip(user_id):
        return True
    
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT daily_count, last_request_date FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    if not res:
        cursor.execute("INSERT INTO users (user_id, daily_count, last_request_date) VALUES (?, 1, ?)", (user_id, today))
        conn.commit()
        return True
    
    daily_count, last_date = res
    if last_date != today:
        cursor.execute("UPDATE users SET daily_count = 1, last_request_date = ? WHERE user_id = ?", (today, user_id))
        conn.commit()
        return True
    else:
        if daily_count < 2:
            cursor.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
        else:
            return False

def get_card_number():
    cursor.execute("SELECT value FROM settings WHERE key = 'card_number'")
    res = cursor.fetchone()
    return res[0] if res else "Karta raqami kiritilmagan"

# --- KEYBOARDS ---
def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Kanalga a'zo bo'lish", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Tekshirish", callback_data="check_sub")]
    ])

def vip_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 kun - 2 000 so'm", callback_data="vip_1")],
        [InlineKeyboardButton(text="1 oy - 10 000 so'm", callback_data="vip_30")],
        [InlineKeyboardButton(text="3 oy - 25 000 so'm", callback_data="vip_90")],
        [InlineKeyboardButton(text="6 oy - 45 000 so'm", callback_data="vip_180")]
    ])

# --- HANDLERS ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("SELECT gift_received FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    gift_msg = ""
    if not res:
        expire_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO users (user_id, last_seen, vip_until, gift_received) VALUES (?, ?, ?, 1)",
            (user_id, now_str, expire_date)
        )
        conn.commit()
        gift_msg = "🎁 **Sizga birinchi marta kirganingiz uchun 1 oylik BEPUL VIP status berildi!** Endi 1 oy davomida kinolarni limitsiz yuklab olishingiz mumkin.\n\n"
    else:
        cursor.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now_str, user_id))
        conn.commit()

    if not await is_subscribed(user_id):
        await message.answer(f"{gift_msg}Botdan foydalanish uchun quyidagi kanalga a'zo bo'ling:", reply_markup=subscription_keyboard())
        return
    
    msg = (f"{gift_msg}Xush kelibsiz! Kino kodini yuboring.\n\n"
           "Tariflar haqida ma'lumot olish uchun /vip buyrug'ini yozing.")
    if user_id == ADMIN_ID:
        msg += "\n\n🛠 **Admin buyruqlari:**\nKino yuklash: /add_kino\nVIP berish: /give_vip ID KUN\nAdmin Panel: /panel\nKarta o'zgartirish: /set_card KARTA\nReklama yuborish: /reklama"
    await message.answer(msg)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    touch_user(callback.from_user.id)
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("Obuna tasdiqlandi! Kino kodini yuborishingiz mumkin.")
    else:
        await callback.answer("Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True)

@dp.message(Command("vip"))
async def vip_cmd(message: types.Message):
    touch_user(message.from_user.id)
    await message.answer("VIP tarifni sotib olish uchun muddatni tanlang:", reply_markup=vip_keyboard())

@dp.callback_query(F.data.startswith("vip_"))
async def vip_select(callback: types.CallbackQuery):
    touch_user(callback.from_user.id)
    days = callback.data.split("_")[1]
    card = get_card_number()
    
    text = (f"💳 Karta raqami: `{card}`\n\n"
            f"To'lovni amalga oshiring va {days} kuncha VIP ni sotib oling chek.\n"
            f"To'lovni amalga oshirgan bo'lsangiz, quyidagi tugma orqali adminga so'rov yuboring.")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Adminga jo'natish", callback_data=f"pay_send_{days}")],
        [InlineKeyboardButton(text="Yo'q, qilinmadi", callback_data="pay_cancel")]
    ])
    
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_send_"))
async def pay_send_callback(callback: types.CallbackQuery):
    days = callback.data.split("_")[2]
    user_id = callback.from_user.id
    username = f"@{callback.from_user.username}" if callback.from_user.username else "Mavjud emas"
    
    admin_text = (f"💰 **Yangi VIP So'rov!**\n\n"
                  f"👤 Foydalanuvchi: {username}\n"
                  f"🆔 ID: `{user_id}`\n"
                  f"📅 Tanlangan muddat: {days} kun\n\n"
                  f"To'lov qilinganligini tekshiring va quyidagi tugma orqali VIP bering.")
    
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="VIP berish (Tasdiqlash)", callback_data=f"admin_accept_{user_id}_{days}")],
        [InlineKeyboardButton(text="Rad etish", callback_data=f"admin_reject_{user_id}")]
    ])
    
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=admin_keyboard, parse_mode="Markdown")
        await callback.message.answer("✅ To'lov so'rovingiz adminga yuborildi. Admin tekshirib tez orada VIP statusingizni faollashtiradi.")
    except Exception:
        await callback.message.answer("❌ So'rov yuborishda xatolik yuz berdi.")
    
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "pay_cancel")
async def pay_cancel_callback(callback: types.CallbackQuery):
    await callback.message.answer("❌ To'lov bekor qilindi yoki amalga oshirilmadi.")
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_accept_"))
async def admin_accept_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    parts = callback.data.split("_")
    user_id = int(parts[2])
    days = int(parts[3])
    
    expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (expire_date, user_id))
    conn.commit()
    
    await callback.message.edit_text(f"✅ Foydalanuvchi {user_id} uchun {days} kunlik VIP berildi!")
    try:
        await bot.send_message(chat_id=user_id, text=f"🎉 Tabriklaymiz! To'lovingiz tasdiqlandi. Admin sizga {days} kunlik VIP status berdi.")
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(f"❌ Foydalanuvchi {user_id} ning so'rovi rad etildi.")
    try:
        await bot.send_message(chat_id=user_id, text="❌ To'lov so'rovingiz admin tomonidan rad etildi.")
    except Exception:
        pass
    await callback.answer()

# --- ADMIN PANEL & REKLAMA TIZIMI ---

@dp.message(Command("panel"), F.from_user.id == ADMIN_ID)
async def admin_panel_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(user_id) FROM users")
    total_users = cursor.fetchone()[0]
    card = get_card_number()
    
    text = (f"📊 **Bot Statistikasi va Admin Panel**\n\n"
            f"👥 Bazadagi jami foydalanuvchilar: `{total_users}` ta\n"
            f"💳 Hozirgi karta raqami: `{card}`\n\n"
            f"Reklama yuborish uchun buyruq: `/reklama`")
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("set_card"), F.from_user.id == ADMIN_ID)
async def set_card_cmd(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Xato format. Misol: `/set_card 8600123456789012`")
        return
    new_card = args[1].strip()
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'card_number'", (new_card,))
    conn.commit()
    await message.answer(f"✅ Karta raqami muvaffaqiyatli o'zgartirildi:\n`{new_card}`", parse_mode="Markdown")

@dp.message(Command("reklama"), F.from_user.id == ADMIN_ID)
async def start_ad(message: types.Message, state: FSMContext):
    await message.answer("📝 Reklama xabarini yuboring (Matn, Rasm, Video yoki ixtiyoriy formatda):")
    await state.set_state(AdminAd.waiting_for_ad)

@dp.message(AdminAd.waiting_for_ad, F.from_user.id == ADMIN_ID)
async def send_ad_to_all(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("⏳ Reklama tarqatilmoqda, kuting...")
    
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            success += 1
            await asyncio.sleep(0.05)  # Telegram limitlariga tushmaslik uchun
        except Exception:
            failed += 1
            
    await message.answer(f"📢 **Reklama tarqatish yakunlandi!**\n\n✅ Muvaffaqiyatli: {success} ta\n❌ Bloklagan/Xato: {failed} ta")

@dp.message(Command("give_vip"), F.from_user.id == ADMIN_ID)
async def give_vip_cmd(message: types.Message):
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("❌ Xato format! Format: `/give_vip USER_ID KUN`")
            return
        user_id = int(args[1])
        days = int(args[2])
        expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (expire_date, user_id))
        conn.commit()
        await message.answer(f"✅ Foydalanuvchi {user_id} muvaffaqiyatli VIP qilindi!")
    except Exception as e:
        await message.answer(f"❌ Xatolik: {str(e)}")

# --- ADMIN PANEL (KINO YUKLASH) ---
@dp.message(Command("add_kino"), F.from_user.id == ADMIN_ID)
async def add_kino_start(message: types.Message, state: FSMContext):
    await message.answer("1-bosqich: Kino nomini kiriting:")
    await state.set_state(MovieUpload.name)

@dp.message(MovieUpload.name, F.from_user.id == ADMIN_ID)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("2-bosqich: Kino uchun kod belgilang:")
    await state.set_state(MovieUpload.code)

@dp.message(MovieUpload.code, F.from_user.id == ADMIN_ID)
async def process_code(message: types.Message, state: FSMContext):
    await state.update_data(code=message.text)
    await message.answer("3-bosqich: Kino qaysi davlatda ishlab chiqarilgan?")
    await state.set_state(MovieUpload.country)

@dp.message(MovieUpload.country, F.from_user.id == ADMIN_ID)
async def process_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    await message.answer("4-bosqich: Kino qaysi tilda?")
    await state.set_state(MovieUpload.lang)

@dp.message(MovieUpload.lang, F.from_user.id == ADMIN_ID)
async def process_lang(message: types.Message, state: FSMContext):
    await state.update_data(lang=message.text)
    await message.answer("5-bosqich: Kino tiniqligi necha px? (Chunonam: 720p, 1080p):")
    await state.set_state(MovieUpload.resolution)

@dp.message(MovieUpload.resolution, F.from_user.id == ADMIN_ID)
async def process_resolution(message: types.Message, state: FSMContext):
    await state.update_data(resolution=message.text)
    await message.answer("Oxirgi bosqich: Kinoning o'zini (Video yoki File shaklida) yuboring:")
    await state.set_state(MovieUpload.video)

@dp.message(MovieUpload.video, F.from_user.id == ADMIN_ID, F.video | F.document)
async def process_video(message: types.Message, state: FSMContext):
    file_id = message.video.file_id if message.video else message.document.file_id
    data = await state.get_data()
    try:
        cursor.execute(
            "INSERT INTO movies (code, file_id, name, country, lang, resolution) VALUES (?, ?, ?, ?, ?, ?)",
            (data['code'], file_id, data['name'], data['country'], data['lang'], data['resolution'])
        )
        conn.commit()
        await message.answer(f"Kino muvaffaqiyatli saqlandi!\n\nKod: {data['code']}\nNomi: {data['name']}")
    except sqlite3.IntegrityError:
        await message.answer("Xatolik: Bu kod bilan avval kino saqlangan. Qayta urining: /add_kino")
    await state.clear()

# --- KINO QIDIRISH VA YUBORISH ---
@dp.message(F.text)
async def search_movie(message: types.Message):
    touch_user(message.from_user.id)
    if not await is_subscribed(message.from_user.id):
        await message.answer(f"Botdan foydalanish uchun quyidagi kanalga a'zo bo'ling:", reply_markup=subscription_keyboard())
        return

    code = message.text.strip()
    cursor.execute("SELECT file_id, name, country, lang, resolution FROM movies WHERE code = ?", (code,))
    movie = cursor.fetchone()
    
    if movie:
        if not check_limit_and_update(message.from_user.id):
            await message.answer("Kunlik 2 ta kino yuklash limitiz tugadi. Cheksiz yuklash uchun /vip tarifini sotib oling.")
            return
        
        file_id, name, country, lang, resolution = movie
        caption = (f"🎬 **Kino nomi:** {name}\n"
                   f"🌍 **Davlat:** {country}\n"
                   f"🌐 **Til:** {lang}\n"
                   f"🖥 **Tiniqlik:** {resolution}\n"
                   f"🔑 **Kod:** {code}")
        await bot.send_video(chat_id=message.chat.id, video=file_id, caption=caption, parse_mode="Markdown")
    else:
        await message.answer("Bu kod bilan kino topilmadi. Kodni to'g'ri yozganingizni tekshiring.")

# --- RENDER WEB SERVER ---
async def handle(request):
    return web.Response(text="Bot is running successfully!")

# --- 72 SOATDA BIR ENLATMA BERUVCHI FON VAZIFASI ---
async def periodic_reminder():
    while True:
        try:
            cursor.execute("SELECT user_id, last_seen FROM users WHERE last_seen IS NOT NULL")
            users = cursor.fetchall()
            now = datetime.now()
            
            for user_id, last_seen_str in users:
                try:
                    last_seen = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                    if now - last_seen >= timedelta(hours=72):
                        await bot.send_message(
                            chat_id=user_id, 
                            text="👋 Salom! Bot esingizdan chiqib ketmadimi? Yangi ajoyib kinolar qo'shildi, kod yuborib ko'rishingiz mumkin! 🎬"
                        )
                        touch_user(user_id)
                except Exception:
                    continue
        except Exception as e:
            print(f"Eslatma tizimida xatolik: {e}")
            
        await asyncio.sleep(1800)

async def main():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get('/', handle)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    print("Bot Render-da muvaffaqiyatli ishga tushdi...")
    asyncio.create_task(periodic_reminder())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

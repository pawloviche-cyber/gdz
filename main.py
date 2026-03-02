"""
ГДЗ-бот — Telegram-бот для решения домашних заданий + Admin Panel
Стек: aiogram 3.25, Telegram Bot API 9.4, Groq API, aiosqlite

Установка зависимостей:
    pip install aiogram==3.25.0 groq python-dotenv aiosqlite

Файл .env рядом с ботом:
    BOT_TOKEN=ваш_токен_бота
    GROQ_API_KEY=ваш_ключ_groq
"""

from __future__ import annotations

import asyncio
import logging
import os
import html
import re
import aiosqlite
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any

from dotenv import load_dotenv

# ── aiogram 3.25 ────────────────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, BaseFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError

# ── Groq SDK ─────────────────────────────────────────────────────────────────
from groq import AsyncGroq

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("Задайте BOT_TOKEN и GROQ_API_KEY в файле .env")

# ═══════════════════════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ БОТА И АДМИНКИ
# ═══════════════════════════════════════════════════════════════════════════════
ADMIN_ID = 8513112712
DB_NAME = "bot.db"

MODEL = "openai/gpt-oss-120b"          
MAX_TOKENS = 8192
TELEGRAM_MSG_LIMIT = 4096              

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("gdz_bot")

# ═══════════════════════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ (SQLite)
# ═══════════════════════════════════════════════════════════════════════════════

async def init_db():
    """Инициализация таблиц базы данных."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                grade TEXT,
                task TEXT,
                answer TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def add_user(user_id: int, username: str, full_name: str):
    """Добавляет пользователя в БД, если его там нет."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name)
        )
        await db.commit()

async def save_history(user_id: int, subject: str, grade: str, task: str, answer: str):
    """Сохраняет запрос и ответ в историю."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO history (user_id, subject, grade, task, answer) VALUES (?, ?, ?, ?, ?)",
            (user_id, subject, grade, task, answer)
        )
        await db.commit()

async def get_user_stats(user_id: int) -> int:
    """Возвращает количество решений конкретного пользователя."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,)) as cursor:
            res = await cursor.fetchone()
            return res[0] if res else 0

async def get_admin_stats() -> dict:
    """Возвращает общую статистику для админа."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM history") as cursor:
            tasks_count = (await cursor.fetchone())[0]
    return {"users": users_count, "tasks": tasks_count}

async def get_all_users() -> list[int]:
    """Возвращает список ID всех пользователей для рассылки."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            return [row[0] for row in await cursor.fetchall()]

# ═══════════════════════════════════════════════════════════════════════════════
#  ПРЕДМЕТЫ И КЛАССЫ
# ═══════════════════════════════════════════════════════════════════════════════

class Subject(str, Enum):
    MATH      = "subj_math"
    PHYSICS   = "subj_physics"
    CHEMISTRY = "subj_chemistry"
    BIOLOGY   = "subj_biology"
    RUSSIAN   = "subj_russian"
    ENGLISH   = "subj_english"
    HISTORY   = "subj_history"
    IT        = "subj_it"
    LITERATURE = "subj_literature"
    OTHER     = "subj_other"

SUBJECT_LABELS: Dict[str, str] = {
    Subject.MATH:       "📐 Математика / Алгебра",
    Subject.PHYSICS:    "⚛️ Физика",
    Subject.CHEMISTRY:  "🧪 Химия",
    Subject.BIOLOGY:    "🧬 Биология",
    Subject.RUSSIAN:    "📖 Русский язык",
    Subject.ENGLISH:    "🇬🇧 Английский язык",
    Subject.HISTORY:    "🏛 История / Общество",
    Subject.IT:         "💻 Информатика",
    Subject.LITERATURE: "📚 Литература",
    Subject.OTHER:      "🔮 Другой предмет",
}

SUBJECT_PROMPTS: Dict[str, str] = {
    Subject.MATH:       "математике (алгебре, геометрии)",
    Subject.PHYSICS:    "физике",
    Subject.CHEMISTRY:  "химии",
    Subject.BIOLOGY:    "биологии",
    Subject.RUSSIAN:    "русскому языку",
    Subject.ENGLISH:    "английскому языку",
    Subject.HISTORY:    "истории и обществознанию",
    Subject.IT:         "информатике и программированию",
    Subject.LITERATURE: "литературе",
    Subject.OTHER:      "указанному предмету",
}

class Grade(str, Enum):
    G1_4   = "grade_1_4"
    G5_6   = "grade_5_6"
    G7_8   = "grade_7_8"
    G9     = "grade_9"
    G10_11 = "grade_10_11"
    UNIVERSITY = "grade_uni"

GRADE_LABELS: Dict[str, str] = {
    Grade.G1_4:       "1–4 класс",
    Grade.G5_6:       "5–6 класс",
    Grade.G7_8:       "7–8 класс",
    Grade.G9:         "9 класс (ОГЭ)",
    Grade.G10_11:     "10–11 класс (ЕГЭ)",
    Grade.UNIVERSITY: "🎓 Университет",
}

GRADE_PROMPTS: Dict[str, str] = {
    Grade.G1_4:       "1–4 класса начальной школы",
    Grade.G5_6:       "5–6 класса",
    Grade.G7_8:       "7–8 класса",
    Grade.G9:         "9 класса (уровень ОГЭ)",
    Grade.G10_11:     "10–11 класса (уровень ЕГЭ)",
    Grade.UNIVERSITY: "университетского курса",
}

# ═══════════════════════════════════════════════════════════════════════════════
#  FSM — состояния диалога
# ═══════════════════════════════════════════════════════════════════════════════

class SolveFlow(StatesGroup):
    choose_subject = State()
    choose_grade   = State()
    enter_task     = State()
    waiting_ai     = State()

class AdminFlow(StatesGroup):
    waiting_for_broadcast = State()

# ═══════════════════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════════════════════

def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Решить задание")],
            [KeyboardButton(text="📚 История решений"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def subjects_inline_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=label, callback_data=subj.value)] for subj, label in SUBJECT_LABELS.items()]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def grades_inline_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=label, callback_data=grade.value)] for grade, label in GRADE_LABELS.items()]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к предмету", callback_data="back_to_subject")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def after_solve_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новое задание", callback_data="new_task")],
        [InlineKeyboardButton(text="❓ Уточнить / Спросить ещё", callback_data="followup")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])

def cancel_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]])

def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="💾 Скачать БД", callback_data="admin_export")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ])

def admin_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить рассылку", callback_data="admin_cancel_broadcast")]])

# ═══════════════════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ И ИИ
# ═══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(subject_key: str, grade_key: str) -> str:
    subj  = SUBJECT_PROMPTS.get(subject_key, "указанному предмету")
    grade = GRADE_PROMPTS.get(grade_key, "школьного курса")
    return (
        f"Ты — опытный преподаватель и репетитор по {subj} для учеников {grade}.\n"
        "Твоя задача — решить задание ученика максимально подробно и понятно.\n\n"
        "ПРАВИЛА:\n"
        "1. Сначала кратко перефразируй условие задачи своими словами (раздел «📋 <b>Условие</b>»).\n"
        "2. Запиши раздел «💡 <b>Идея решения</b>» — в 1-2 предложениях объясни подход.\n"
        "3. Дай пошаговое решение (раздел «📝 <b>Решение</b>»), каждый шаг нумеруй.\n"
        "   — математические формулы и вычисления оборачивай в тег <code>.\n"
        "4. Запиши финальный «✅ <b>Ответ</b>» отдельной строкой.\n"
        "5. Отвечай ТОЛЬКО на русском языке.\n"
        "6. Используй ТОЛЬКО теги <b>, <i>, <code>. ЗАПРЕЩЕНО использовать списки (<ol>, <ul>, <li>) и <br>.\n"
        "7. НИКОГДА не используй разметку LaTeX (запрещены теги \\[, \\], \\(, \\)).\n"
        "8. Будь дружелюбным и поддерживающим.\n"
    )

def fix_format(text: str) -> str:
    text = text.replace("\\[", "\n").replace("\\]", "\n").replace("\\(", "").replace("\\)", "")
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?ul>|</?ol>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li>', '- ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^###?\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'<(?!/?(?:b|i|u|s|a|code|pre|tg-spoiler|blockquote)\b)', '&lt;', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

@dataclass
class Conversation:
    messages: list = field(default_factory=list)

conversations: Dict[int, Conversation] = {}

async def ask_ai(user_id: int, user_text: str, system_prompt: str) -> str:
    conv = conversations.setdefault(user_id, Conversation())
    if not conv.messages or conv.messages[0].get("role") != "system":
        conv.messages.insert(0, {"role": "system", "content": system_prompt})
    
    conv.messages.append({"role": "user", "content": user_text})
    if len(conv.messages) > 21:
        conv.messages = [conv.messages[0]] + conv.messages[-20:]

    client = AsyncGroq(api_key=GROQ_API_KEY)
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=conv.messages,
            temperature=0.3,
            max_completion_tokens=MAX_TOKENS,
            reasoning_effort="high",
        )
        raw_answer = resp.choices[0].message.content or "⚠️ Модель вернула пустой ответ."
        answer = fix_format(raw_answer)
    except Exception as e:
        log.exception("Groq API error")
        answer = f"⚠️ Ошибка при обращении к ИИ:\n<code>{html.escape(str(e))}</code>"

    conv.messages.append({"role": "assistant", "content": answer})
    return answer

def split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    if len(text) <= limit: return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line + "\n"
        else: buf += line + "\n"
    if buf.strip(): parts.append(buf)
    return parts

# ═══════════════════════════════════════════════════════════════════════════════
#  РОУТЕРЫ
# ═══════════════════════════════════════════════════════════════════════════════
router = Router(name="gdz")
admin_router = Router(name="admin")

class IsAdmin(BaseFilter):
    async def __call__(self, message: Message | CallbackQuery) -> bool:
        return message.from_user.id == ADMIN_ID

# ── АДМИН ПАНЕЛЬ ─────────────────────────────────────────────────────────────

@admin_router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚙️ <b>Панель администратора</b>\nВыберите действие ниже:",
        reply_markup=admin_panel_kb()
    )

@admin_router.callback_query(F.data == "admin_stats", IsAdmin())
async def admin_stats(callback: CallbackQuery):
    stats = await get_admin_stats()
    text = (
        "📊 <b>Статистика бота:</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['users']}</b>\n"
        f"✅ Решенных задач: <b>{stats['tasks']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_panel_kb())
    await callback.answer()

@admin_router.callback_query(F.data == "admin_export", IsAdmin())
async def admin_export(callback: CallbackQuery):
    await callback.answer("Подготовка файла БД...")
    file = FSInputFile(DB_NAME)
    await callback.message.answer_document(document=file, caption="💾 Актуальный дамп базы данных.")

@admin_router.callback_query(F.data == "admin_broadcast", IsAdmin())
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_for_broadcast)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\nОтправьте сообщение (текст, фото или видео), которое нужно разослать всем пользователям бота.",
        reply_markup=admin_cancel_kb()
    )
    await callback.answer()

@admin_router.callback_query(F.data == "admin_cancel_broadcast", IsAdmin())
async def admin_cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("⚙️ <b>Панель администратора</b>", reply_markup=admin_panel_kb())
    await callback.answer("Рассылка отменена")

@admin_router.callback_query(F.data == "admin_close", IsAdmin())
async def admin_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@admin_router.message(AdminFlow.waiting_for_broadcast, IsAdmin())
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    users = await get_all_users()
    success, failed = 0, 0
    
    status_msg = await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    
    for user_id in users:
        try:
            await message.send_copy(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05) # Защита от флуд-контроля Telegram
        except TelegramForbiddenError:
            failed += 1 # Пользователь заблокировал бота
        except Exception:
            failed += 1
            
    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Успешно: {success}\n"
        f"Не удалось (заблокировали): {failed}"
    )

# ── ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ ───────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await add_user(
        user_id=message.from_user.id, 
        username=message.from_user.username or "", 
        full_name=message.from_user.full_name
    )
    await state.clear()
    conversations.pop(message.from_user.id, None)
    await message.answer(
        "👋 <b>Привет! Я — бот-ГДЗ.</b>\n\n"
        "Я помогу тебе подробно решить любое школьное или университетское задание.\n\n"
        "Нажми <b>«📝 Решить задание»</b>, чтобы начать!",
        reply_markup=main_reply_kb(),
    )

@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "📚 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«📝 Решить задание»</b>\n"
        "2️⃣ Выбери <b>предмет</b> и <b>класс</b>\n"
        "3️⃣ Напиши условие задачи\n"
        "4️⃣ Получи <b>подробное пошаговое решение</b> ✨\n\n"
        "🤖 Модель: <code>openai/gpt-oss-120b</code>",
        reply_markup=main_reply_kb(),
    )

@router.message(F.text == "📚 История решений")
async def cmd_history(message: Message):
    count = await get_user_stats(message.from_user.id)
    if count == 0:
        await message.answer("🗂 У тебя пока нет решенных заданий. Нажми «📝 Решить задание», чтобы начать!")
    else:
        await message.answer(f"🗂 За всё время ты решил(а) задач вместе со мной: <b>{count}</b> шт.\nТак держать! 🚀")

@router.message(F.text == "📝 Решить задание")
async def start_solve(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SolveFlow.choose_subject)
    await message.answer("📚 <b>Выбери предмет:</b>", reply_markup=subjects_inline_kb())

@router.callback_query(SolveFlow.choose_subject, F.data.startswith("subj_"))
async def on_subject_chosen(callback: CallbackQuery, state: FSMContext):
    subject = callback.data
    await state.update_data(subject=subject, subject_label=SUBJECT_LABELS.get(subject, subject))
    await state.set_state(SolveFlow.choose_grade)
    await callback.message.edit_text(f"✅ Предмет: <b>{SUBJECT_LABELS.get(subject, subject)}</b>\n\n🎓 <b>Теперь выбери класс:</b>", reply_markup=grades_inline_kb())
    await callback.answer()

@router.callback_query(SolveFlow.choose_grade, F.data == "back_to_subject")
async def back_to_subject(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SolveFlow.choose_subject)
    await callback.message.edit_text("📚 <b>Выбери предмет:</b>", reply_markup=subjects_inline_kb())
    await callback.answer()

@router.callback_query(SolveFlow.choose_grade, F.data.startswith("grade_"))
async def on_grade_chosen(callback: CallbackQuery, state: FSMContext):
    grade = callback.data
    await state.update_data(grade=grade, grade_label=GRADE_LABELS.get(grade, grade))
    await state.set_state(SolveFlow.enter_task)
    data = await state.get_data()
    await callback.message.edit_text(
        f"✅ Предмет: <b>{data['subject_label']}</b>\n"
        f"✅ Класс: <b>{GRADE_LABELS.get(grade, grade)}</b>\n\n"
        "✏️ <b>Теперь отправь условие задания</b> текстом.",
        reply_markup=cancel_inline_kb(),
    )
    await callback.answer()

@router.message(SolveFlow.enter_task, F.text)
async def on_task_received(message: Message, state: FSMContext):
    data = await state.get_data()
    subject = data.get("subject", Subject.OTHER)
    grade   = data.get("grade", Grade.G7_8)
    task    = message.text.strip()

    await state.set_state(SolveFlow.waiting_ai)
    thinking_msg = await message.answer("🧠 <b>Думаю над решением…</b>\n⏳ Это может занять 10-30 секунд.")

    system_prompt = build_system_prompt(subject, grade)
    answer = await ask_ai(message.from_user.id, task, system_prompt)
    
    # Сохраняем в БД историю решений
    await save_history(
        user_id=message.from_user.id,
        subject=SUBJECT_LABELS.get(subject, "Неизвестно"),
        grade=GRADE_LABELS.get(grade, "Неизвестно"),
        task=task,
        answer=answer
    )

    try: await thinking_msg.delete()
    except Exception: pass

    parts = split_message(answer)
    for i, part in enumerate(parts):
        kwargs = {"reply_markup": after_solve_kb()} if i == len(parts) - 1 else {}
        try:
            await message.answer(part, **kwargs)
        except Exception as e:
            await message.answer(part, parse_mode=None, **kwargs)

    await state.set_state(SolveFlow.enter_task)

@router.callback_query(F.data == "followup")
async def on_followup(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SolveFlow.enter_task)
    await callback.message.answer("❓ <b>Задай уточняющий вопрос</b> — я учту предыдущее решение.", reply_markup=cancel_inline_kb())
    await callback.answer()

@router.callback_query(F.data == "new_task")
async def on_new_task(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    conversations.pop(callback.from_user.id, None)
    await state.set_state(SolveFlow.choose_subject)
    await callback.message.answer("📚 <b>Выбери предмет:</b>", reply_markup=subjects_inline_kb())
    await callback.answer()

@router.callback_query(F.data == "main_menu")
async def on_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("🏠 <b>Главное меню</b>\nНажми «📝 Решить задание», чтобы начать.", reply_markup=main_reply_kb())
    await callback.answer()

@router.message(SolveFlow.enter_task, F.photo)
async def on_photo_task(message: Message):
    await message.answer("📷 Пока я принимаю только <b>текстовые</b> задания. Пожалуйста, перепиши условие текстом ✏️", reply_markup=cancel_inline_kb())

@router.message(F.text)
async def fallback(message: Message):
    await message.answer("👆 Используй кнопки ниже или нажми <b>«📝 Решить задание»</b>.", reply_markup=main_reply_kb())

# ═══════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    await init_db() # Создаем базу данных перед запуском
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    
    dp.include_router(admin_router) # Админский роутер должен быть выше, чтобы перехватывать команды
    dp.include_router(router)

    log.info("🚀 Бот ГДЗ запускается с поддержкой SQLite и Админ-панели…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
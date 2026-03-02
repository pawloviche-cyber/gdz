from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Final

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from groq import AsyncGroq

# ──────────────────────────────────────────────
# Токены — вставь свои значения
# ──────────────────────────────────────────────
BOT_TOKEN: Final[str] = "8744674548:AAGR7pj2dKyNK86BRpp616kcz7UOspAInok"
GROQ_API_KEY: Final[str] = "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"

# ──────────────────────────────────────────────
# Настройки модели
# ──────────────────────────────────────────────
MODEL_ID: Final[str] = "openai/gpt-oss-120b"
REASONING_EFFORT: Final[str] = "high"
MAX_COMPLETION_TOKENS: Final[int] = 16384
TEMPERATURE: Final[float] = 0.6

# ──────────────────────────────────────────────
# Системный промпт
# ──────────────────────────────────────────────
SYSTEM_PROMPT: Final[str] = """\
Ты — опытный репетитор для школьников и студентов (5–11 класс, 1–2 курс). Помогаешь с домашними заданиями по любым предметам.

━━━ ФОРМАТ ОТВЕТА ━━━

Ответ ВСЕГДА строй по этой структуре:

📋 Дано
Кратко перечисли известные данные из условия.

🔍 Решение
Пошаговое решение. Каждый шаг — отдельный пронумерованный блок.
В каждом шаге: что делаем → почему → вычисление/действие → промежуточный результат.

✅ Ответ
Чёткий финальный ответ, выделенный жирным.

━━━ КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА ФОРМАТИРОВАНИЯ ━━━

Ты пишешь для Telegram-мессенджера. Telegram НЕ поддерживает LaTeX, MathJax, KaTeX и Markdown-заголовки.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать:
- \\frac{}{}, \\text{}, \\cdot, \\times, \\boxed{}, \\sqrt{}, \\left, \\right
- \\[ \\], \\( \\), $$ $$, $ $
- Любые команды с обратным слэшем
- Заголовки: #, ##, ###

ВМЕСТО ЭТОГО используй:
- Дроби: пиши текстом — (12 Н) / (6 кг) = 2 м/с²
- Умножение: используй символ ×  или  ·
- Степени: используй Unicode — м/с², x², м³, 10⁴
- Корни: пиши √(25) = 5
- Индексы: используй Unicode — m₁, a₂, v₀, F₃
- Жирный текст: *вот так*
- Курсив: _вот так_

Пример ПРАВИЛЬНОГО оформления:
  F = m · a = 4 кг × 3 м/с² = 12 Н
  a₂ = F / m₂ = 12 Н / 6 кг = *2 м/с²*

Пример НЕПРАВИЛЬНОГО оформления (НИКОГДА так не пиши):
  F = m \\cdot a = 4\\ \\text{кг} \\times 3\\ \\text{м/с}^2 = 12\\ \\text{Н}

━━━ СТИЛЬ ━━━

- Объясняй простым языком, как будто рассказываешь другу.
- Если шаг очевидный — всё равно покажи его (ученику может быть неочевидно).
- Используй эмодзи-маркеры (📋, 🔍, ✅, 📌) вместо заголовков.
- Пиши единицы измерения словами или стандартными символами: кг, м/с², Н, Дж, моль.
- Если задание неполное или непонятное — вежливо попроси уточнить.
- Отвечай ТОЛЬКО на русском.
- Не придумывай условия — работай строго с тем, что написал пользователь.\
"""

# ──────────────────────────────────────────────
# Логирование
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Инициализация
# ──────────────────────────────────────────────
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
router = Router(name="gdz")

MAX_TG_MESSAGE_LEN: Final[int] = 4096


# ──────────────────────────────────────────────
# Постпроцессинг: очистка остаточного LaTeX
# Страховка на случай, если модель проигнорирует
# инструкции промпта.
# ──────────────────────────────────────────────

# Таблицы Unicode-замен для степеней и индексов
_SUPERSCRIPTS: Final[dict[str, str]] = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "n": "ⁿ", "+": "⁺", "-": "⁻",
}

_SUBSCRIPTS: Final[dict[str, str]] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "a": "ₐ", "e": "ₑ", "i": "ᵢ", "n": "ₙ", "o": "ₒ", "x": "ₓ",
}

# LaTeX-команды → Unicode-символы
_LATEX_SYMBOLS: Final[dict[str, str]] = {
    r"\cdot": "·", r"\times": "×", r"\approx": "≈",
    r"\neq": "≠", r"\leq": "≤", r"\geq": "≥",
    r"\pm": "±", r"\infty": "∞", r"\pi": "π",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ",
    r"\delta": "δ", r"\Delta": "Δ", r"\lambda": "λ",
    r"\mu": "μ", r"\omega": "ω", r"\Omega": "Ω",
    r"\theta": "θ", r"\sigma": "σ", r"\sum": "Σ",
    r"\int": "∫", r"\rightarrow": "→", r"\leftarrow": "←",
    r"\Rightarrow": "⇒", r"\quad": " ", r"\qquad": "  ",
    r"\,": " ", r"\ ": " ",
}


def sanitize_response(text: str) -> str:
    """Убирает остаточный LaTeX и невалидный Markdown из ответа модели."""

    # 1. Блочные LaTeX-обёртки: \[ ... \] и $$ ... $$
    text = re.sub(r"\\\[(.+?)\\\]", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\$\$(.+?)\$\$", r"\1", text, flags=re.DOTALL)

    # 2. Инлайновые LaTeX-обёртки: \( ... \) и $ ... $
    text = re.sub(r"\\\((.+?)\\\)", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", r"\1", text)

    # 3. \frac{a}{b} → (a)/(b)
    # Обрабатываем вложенные frac рекурсивно (до 5 уровней)
    for _ in range(5):
        new_text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
        if new_text == text:
            break
        text = new_text

    # 4. \sqrt{x} → √(x)
    text = re.sub(r"\\sqrt\{([^}]*)\}", r"√(\1)", text)

    # 5. \boxed{x} → 【x】
    text = re.sub(r"\\boxed\{([^}]*)\}", r"【\1】", text)

    # 6. \text{кг} → кг,  \mathrm{...} → ...
    text = re.sub(r"\\(?:text|mathrm|mathbf|mathit)\{([^}]*)\}", r"\1", text)

    # 7. Символьные замены
    for latex_cmd, unicode_char in _LATEX_SYMBOLS.items():
        text = text.replace(latex_cmd, unicode_char)

    # 8. Степени: ^{23} → ²³,  ^2 → ²
    def _replace_superscript_group(m: re.Match) -> str:
        return "".join(_SUPERSCRIPTS.get(c, c) for c in m.group(1))

    def _replace_superscript_single(m: re.Match) -> str:
        c = m.group(1)
        return _SUPERSCRIPTS.get(c, f"^{c}")

    text = re.sub(r"\^\{([^}]*)\}", _replace_superscript_group, text)
    text = re.sub(r"\^(\d)", _replace_superscript_single, text)

    # 9. Индексы: _{12} → ₁₂,  _2 → ₂
    def _replace_subscript_group(m: re.Match) -> str:
        return "".join(_SUBSCRIPTS.get(c, c) for c in m.group(1))

    def _replace_subscript_single(m: re.Match) -> str:
        c = m.group(1)
        return _SUBSCRIPTS.get(c, f"_{c}")

    text = re.sub(r"_\{([^}]*)\}", _replace_subscript_group, text)
    text = re.sub(r"_(\d)", _replace_subscript_single, text)

    # 10. Оставшиеся LaTeX-команды (\command) — удалить
    text = re.sub(r"\\[a-zA-Z]+", "", text)

    # 11. Оставшиеся фигурные скобки от LaTeX
    text = text.replace("{", "").replace("}", "")

    # 12. Markdown-заголовки → жирный текст
    text = re.sub(r"^#{1,3}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # 13. Разделители --- → пустая строка
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)

    # 14. Убираем лишние пустые строки
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ──────────────────────────────────────────────
# Отправка длинных сообщений
# ──────────────────────────────────────────────
async def send_long_message(message: Message, text: str) -> None:
    """Разбивает текст на части ≤4096 символов и отправляет."""
    for start in range(0, len(text), MAX_TG_MESSAGE_LEN):
        chunk = text[start : start + MAX_TG_MESSAGE_LEN]
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try:
                await message.answer(chunk, parse_mode=None)
            except Exception as exc:
                logger.error("Не удалось отправить чанк: %s", exc)


# ──────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────
@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    name = message.from_user.full_name
    welcome = (
        f"Привет, *{name}*! 👋\n\n"
        "Я — бот-помощник с домашними заданиями.\n\n"
        "Просто отправь мне текст задания, и я дам "
        "подробное пошаговое решение с ответом.\n\n"
        "📚 Поддерживаю: математику, физику, химию, "
        "русский, английский, историю, биологию и другие предметы.\n\n"
        "📌 Команды:\n"
        "/start — приветствие\n"
        "/help — как пользоваться"
    )
    try:
        await message.answer(welcome, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await message.answer(welcome, parse_mode=None)


# ──────────────────────────────────────────────
# /help
# ──────────────────────────────────────────────
@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    help_text = (
        "📖 *Как пользоваться ботом:*\n\n"
        "1. Отправь текст задания — я решу его пошагово.\n"
        "2. Чем подробнее условие, тем точнее ответ.\n"
        "3. Можно отправлять несколько заданий подряд.\n\n"
        "💡 *Советы:*\n"
        "• Указывай класс и предмет для лучшего ответа.\n"
        "• Пиши формулы текстом: «корень из 16», «x² + 3x».\n"
        "• Если ответ кажется неверным — перефразируй задание."
    )
    try:
        await message.answer(help_text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await message.answer(help_text, parse_mode=None)


# ──────────────────────────────────────────────
# Основной обработчик заданий
# ──────────────────────────────────────────────
@router.message(F.text)
async def handle_task(message: Message) -> None:
    user_text = message.text.strip()

    if not user_text:
        await message.answer("Отправь текст задания, и я помогу решить! 📝")
        return

    thinking_msg = await message.answer("⏳ Решаю задание, подожди немного...")

    try:
        completion = await groq_client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            reasoning_effort=REASONING_EFFORT,
            temperature=TEMPERATURE,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            stream=False,
        )

        answer = completion.choices[0].message.content

        if not answer:
            await thinking_msg.edit_text(
                "Не удалось получить ответ. Попробуй переформулировать задание."
            )
            return

        # Постпроцессинг: гарантированно убираем LaTeX
        answer = sanitize_response(answer)

        await thinking_msg.delete()
        await send_long_message(message, answer)

        # Лог расхода токенов
        usage = completion.usage
        if usage:
            logger.info(
                "user=%s | prompt=%d | completion=%d | total=%d",
                message.from_user.id,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
            )

    except Exception as exc:
        logger.exception("Ошибка Groq API: %s", exc)
        try:
            await thinking_msg.edit_text(
                "❌ Ошибка при решении. Попробуй ещё раз через несколько секунд."
            )
        except Exception:
            await message.answer(
                "❌ Ошибка при решении. Попробуй ещё раз через несколько секунд."
            )


# ──────────────────────────────────────────────
# Обработчик фото
# ──────────────────────────────────────────────
@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    await message.answer(
        "📷 Я пока не умею читать фото.\n"
        "Перепиши задание текстом — и я его решу!"
    )


# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────
async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Бот запущен. Ожидание сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

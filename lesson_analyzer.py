"""
CryptoRadar — Анализатор сделок (REVIEW_MODEL — Claude Sonnet).
Разбор каждой закрытой сделки (TP и SL) + генерация советов для AI.
"""

import json
from openai import OpenAI

import config
import database
from logger import log
from models import Order


_REVIEW_SYSTEM_PROMPT = """Ты — профессиональный аналитик торговых сделок. Тебе передают данные о закрытой крипто-сделке и график. Твоя задача — сделать полный разбор.

ОТВЕТ ПИШИ СТРОГО В PLAIN TEXT. ЗАПРЕЩЕНО использовать Markdown-разметку.

Формат ответа:

🔍 РАЗБОР СДЕЛКИ
(Полный анализ без ограничения по длине — пиши столько, сколько нужно)

В конце ОБЯЗАТЕЛЬНО добавь ровно 2 совета в ТОЧНОМ формате:

СОВЕТ_АНАЛИЗАТОРУ: (1-2 предложения — что первичный анализатор монеты должен учитывать при анализе в будущем)

СОВЕТ_ТРЕЙДЕРУ: (1-2 предложения — что генератор ордеров должен учитывать при расстановке entry/SL/TP в будущем)

ВАЖНО: советы должны быть УНИКАЛЬНЫМИ и НЕ повторять уже данные ранее советы."""


def _get_client() -> OpenAI:
    """Создаёт OpenAI-клиент для OpenRouter."""
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )


def analyze_trade(
    order_data: dict,
    candles_json: str,
    close_reason: str,
) -> tuple[str, str, str]:
    """
    REVIEW_MODEL анализирует закрытую сделку.
    
    Args:
        order_data: данные ордера из SQLite
        candles_json: JSON свечей за период жизни ордера
        close_reason: "tp_hit" или "sl_hit"
    
    Returns:
        (full_analysis, tip_for_analyzer, tip_for_order)
    """
    symbol = order_data["symbol"]
    direction = order_data["direction"]

    # Формируем промпт в зависимости от причины закрытия
    if close_reason == "sl_hit":
        task = (
            "Сделка ЗАКРЫЛАСЬ ПО СТОП-ЛОССУ (убыток).\n\n"
            "Проанализируй:\n"
            "- Что пошло не так?\n"
            "- Какие сигналы были проигнорированы?\n"
            "- Где была ошибка в точке входа или расстановке SL?\n"
            "- Можно ли было предвидеть разворот?"
        )
    else:
        task = (
            "Сделка ЗАКРЫЛАСЬ ПО ТЕЙК-ПРОФИТУ (прибыль).\n\n"
            "Проанализируй:\n"
            "- Что было сделано правильно?\n"
            "- Что можно было сделать лучше?\n"
            "- Был ли профит максимальным или можно было взять больше?\n"
            "- Насколько оптимальной была точка входа?"
        )

    user_prompt = f"""{task}

Монета: {symbol}
Направление: {direction}
Причина закрытия: {close_reason}
Вход: {order_data['entry']}
SL: {order_data['sl']}
TP: {order_data['tp']}
Цена закрытия: {order_data.get('close_price', 'N/A')}
PnL: ${order_data.get('pnl', 0):.2f}
R/R: {order_data.get('rr_ratio', 'N/A')}

Свечи 1h за период сделки:
{candles_json}"""

    # Передаём существующие советы чтобы REVIEW_MODEL не дублировал
    existing_analyzer_tips = database.get_tips("analyzer", config.MAX_TIPS_IN_PROMPT)
    existing_order_tips = database.get_tips("order", config.MAX_TIPS_IN_PROMPT)

    if existing_analyzer_tips or existing_order_tips:
        user_prompt += "\n\n📋 УЖЕ ДАННЫЕ СОВЕТЫ (не повторяй их):"
        if existing_analyzer_tips:
            user_prompt += "\n\nСоветы анализатору:\n" + "\n".join(
                f"- {t}" for t in existing_analyzer_tips
            )
        if existing_order_tips:
            user_prompt += "\n\nСоветы трейдеру:\n" + "\n".join(
                f"- {t}" for t in existing_order_tips
            )

    client = _get_client()

    try:
        log.info(f"REVIEW_MODEL: анализ {symbol} {direction} ({close_reason})")

        response = client.chat.completions.create(
            model=config.REVIEW_MODEL,
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=3000,
            timeout=config.AI_TIMEOUT,
        )

        full_text = response.choices[0].message.content.strip()

        # Парсим советы
        tip_analyzer, tip_order = _parse_tips(full_text)

        log.info(
            f"REVIEW_MODEL: анализ {symbol} готов ({len(full_text)} символов) | "
            f"Совет анализатору: {tip_analyzer[:50]}... | "
            f"Совет трейдеру: {tip_order[:50]}..."
        )

        return full_text, tip_analyzer, tip_order

    except Exception as e:
        log.error(f"REVIEW_MODEL: ошибка анализа {symbol}: {e}")
        return f"Анализ недоступен: {e}", "", ""


def _parse_tips(text: str) -> tuple[str, str]:
    """
    Извлекает советы из ответа REVIEW_MODEL.
    Ищет 'СОВЕТ_АНАЛИЗАТОРУ:' и 'СОВЕТ_ТРЕЙДЕРУ:'.
    """
    tip_analyzer = ""
    tip_order = ""

    lines = text.split("\n")
    for i, line in enumerate(lines):
        line_stripped = line.strip()

        if line_stripped.startswith("СОВЕТ_АНАЛИЗАТОРУ:"):
            tip_analyzer = line_stripped.replace("СОВЕТ_АНАЛИЗАТОРУ:", "").strip()
            # Если совет продолжается на следующей строке
            if not tip_analyzer and i + 1 < len(lines):
                tip_analyzer = lines[i + 1].strip()

        elif line_stripped.startswith("СОВЕТ_ТРЕЙДЕРУ:"):
            tip_order = line_stripped.replace("СОВЕТ_ТРЕЙДЕРУ:", "").strip()
            if not tip_order and i + 1 < len(lines):
                tip_order = lines[i + 1].strip()

    return tip_analyzer, tip_order


def save_lesson_with_tips(
    order_data: dict,
    analysis: str,
    tip_analyzer: str,
    tip_order: str,
    chart_bytes: bytes = None,
):
    """
    Сохраняет урок в БД + советы в ai_tips.
    """
    order_id = order_data["id"]
    symbol = order_data["symbol"]

    # Сохраняем урок
    database.add_lesson(
        order_id=order_id,
        symbol=symbol,
        direction=order_data["direction"],
        entry=order_data["entry"],
        sl=order_data["sl"],
        tp=order_data["tp"],
        close_reason=order_data.get("close_reason", "unknown"),
        pnl=order_data.get("pnl", 0),
        mode=order_data.get("mode", "demo"),
        analysis=analysis,
        chart_snapshot=chart_bytes,
    )

    # Сохраняем советы (если не пустые)
    if tip_analyzer:
        database.add_tip("analyzer", tip_analyzer, order_id, symbol)
    if tip_order:
        database.add_tip("order", tip_order, order_id, symbol)

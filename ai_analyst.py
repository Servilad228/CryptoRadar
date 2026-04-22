"""
CryptoRadar — AI-аналитик (Анализатор через OpenRouter).
Генерирует резюме, извлекает уровни, рассчитывает вероятность.
"""

import json
import re
from openai import OpenAI

import config
import database
from logger import log
from models import ScreenResult, Direction


_SYSTEM_PROMPT = """Ты — профессиональный криптоаналитик. Тебе присылают данные технического анализа монеты с двух таймфреймов (15m и 1h). Направления на обоих ТФ совпадают.

ВАЖНО: Если суточный объем торгов монеты слишком мал (менее 5 000 000 USDT) или по технической картине это явно неликвидный щиткоин (огромные тени, отсутствие объемов, аномальные пампы), верни ТОЛЬКО ОДНО СЛОВО: SHITCOIN_SKIP

ОТВЕТ ПИШИ СТРОГО В PLAIN TEXT. ЗАПРЕЩЕНО использовать Markdown-разметку: НЕ используй ###, **, __, ```, *. Только чистый текст с эмодзи.

Структура ответа:

📝 РЕЗЮМЕ
(5-7 предложений: конкретно почему монета пойдёт в указанном направлении, логика входа, на что обратить внимание)

📊 КЛЮЧЕВЫЕ УРОВНИ
- Поддержка: <точная цена float>
- Сопротивление: <точная цена float>

⚠️ РИСКИ
- (пункт 1)
- (пункт 2)
- (пункт 3)

Пиши лаконично. Уровни — строго числа. Не давай прямых финансовых советов — только анализ данных."""


def _build_user_prompt(screen: ScreenResult, candles_json: str) -> str:
    """Формирует пользовательский промпт с данными монеты."""
    signals_15m = "\n".join(
        f"  • {s}" for s in screen.signals_15m if s.direction == screen.direction
    )
    signals_1h = "\n".join(
        f"  • {s}" for s in screen.signals_1h if s.direction == screen.direction
    )

    prompt = f"""Направление: {screen.direction.value}
Монета: {screen.symbol}
Текущая цена: {screen.last_price}
Суточный объем (24h USDT): {screen.volume_24h}

Анализ 15m (score {screen.score_15m}/10):
{signals_15m}

Анализ 1h (score {screen.score_1h}/10):
{signals_1h}

Последние 50 свечей 1h (OHLCV):
{candles_json}"""

    # Вставляем советы из прошлых сделок
    tips = database.get_tips("analyzer", config.MAX_TIPS_IN_PROMPT)
    if tips:
        tips_text = "\n".join(f"- {t}" for t in tips)
        prompt += f"\n\n📋 Советы с прошлых сделок:\n{tips_text}"

    return prompt


def _get_client() -> OpenAI:
    """Создаёт OpenAI-клиент для OpenRouter."""
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )


def analyze(screen: ScreenResult, klines_1h_df) -> tuple[str, str, dict]:
    """
    Отправляет данные монеты в AI для анализа.
    
    Возвращает (summary, details, levels):
    - summary: секции СИЛА + РЕЗЮМЕ (для caption)
    - details: полный анализ
    - levels: {"support": float, "resistance": float}
    
    Raises: RuntimeError при провале после всех retry.
    """
    # Подготовка данных свечей (последние 50)
    candles = []
    if klines_1h_df is not None and not klines_1h_df.empty:
        tail = klines_1h_df.tail(50)
        for idx, row in tail.iterrows():
            candles.append({
                "time": str(idx),
                "o": round(row["open"], 6),
                "h": round(row["high"], 6),
                "l": round(row["low"], 6),
                "c": round(row["close"], 6),
                "v": round(row["volume"], 2),
            })
    candles_json = json.dumps(candles, ensure_ascii=False)

    user_prompt = _build_user_prompt(screen, candles_json)
    client = _get_client()

    last_error = None

    for attempt in range(1, config.AI_MAX_RETRIES + 1):
        try:
            log.info(f"AI анализ {screen.symbol} (попытка {attempt})...")

            response = client.chat.completions.create(
                model=config.ANALYZER_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=2500,
                timeout=config.AI_TIMEOUT,
            )

            full_text = response.choices[0].message.content.strip()

            if "SHITCOIN_SKIP" in full_text:
                raise RuntimeError("Щиткоин отсеян нейросетью по объему/паттернам (SHITCOIN_SKIP)")

            # Разделяем на summary и details
            summary = _extract_summary(full_text)

            # Извлекаем уровни
            levels = _extract_levels(full_text)

            log.info(f"AI анализ {screen.symbol} — готов ({len(full_text)} символов)")
            return summary, full_text, levels

        except Exception as e:
            last_error = e
            log.warning(f"AI попытка {attempt} для {screen.symbol} провалилась: {e}")

    # Все попытки исчерпаны
    error_msg = f"AI анализ {screen.symbol} провалился после {config.AI_MAX_RETRIES} попыток: {last_error}"
    log.error(error_msg)
    raise RuntimeError(error_msg)


def _extract_summary(text: str) -> str:
    """
    Извлекает краткое резюме из полного ответа.
    Берёт всё до секции КЛЮЧЕВЫЕ УРОВНИ.
    """
    lines = text.split("\n")
    summary_lines = []

    for line in lines:
        if any(marker in line for marker in ["📊 КЛЮЧЕВЫЕ", "📊 УРОВНИ", "⚖️ RISK", "⚖️", "⚠️ РИСКИ", "⚠️"]):
            break
        summary_lines.append(line)

    result = "\n".join(summary_lines).strip()
    # Ограничиваем 900 символами (Telegram caption = 1024, запас для заголовка)
    if len(result) > 900:
        result = result[:897] + "..."
    return result


def _extract_levels(text: str) -> dict:
    """
    Парсит уровни поддержки и сопротивления из ответа AI.
    Returns: {"support": float, "resistance": float}
    Если не найдено — возвращает пустой dict.
    """
    levels = {}

    # Паттерны для поиска уровней
    support_patterns = [
        r"[Пп]оддержка[:\s]+([0-9][0-9,.\s]*[0-9])",
        r"[Ss]upport[:\s]+([0-9][0-9,.\s]*[0-9])",
    ]
    resistance_patterns = [
        r"[Сс]опротивление[:\s]+([0-9][0-9,.\s]*[0-9])",
        r"[Rr]esistance[:\s]+([0-9][0-9,.\s]*[0-9])",
    ]

    for pattern in support_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                val = match.group(1).replace(",", "").replace(" ", "")
                levels["support"] = float(val)
                break
            except ValueError:
                continue

    for pattern in resistance_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                val = match.group(1).replace(",", "").replace(" ", "")
                levels["resistance"] = float(val)
                break
            except ValueError:
                continue

    return levels


def estimate_probability(screen: ScreenResult) -> int:
    """
    Серверный расчёт вероятности (без LLM).
    Базируется на скорах и совпадении индикаторов.
    
    Returns: int (0-95)
    """
    min_score = screen.min_score
    # base: min_score * 10 (3/10 = 30%)
    base = min_score * 10

    # Бонус за совпадение индикаторов на обоих ТФ
    names_15m = {s.name for s in screen.signals_15m if s.direction == screen.direction}
    names_1h = {s.name for s in screen.signals_1h if s.direction == screen.direction}
    overlap = names_15m & names_1h
    bonus = len(overlap) * 5  # +5% за каждый совпавший

    # Бонус за сильный тренд (ADX)
    for s in screen.signals_1h:
        if "ADX" in s.name and s.direction == screen.direction:
            if s.value > 25:
                bonus += 10
            break

    probability = min(base + bonus, 95)
    return max(probability, 10)


def health_check() -> bool:
    """
    Проверка доступности AI (для daily self-test).
    Отправляет простой запрос, возвращает True если API отвечает.
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.ANALYZER_MODEL,
            messages=[
                {"role": "user", "content": "Ответь одним словом: работаешь?"},
            ],
            max_tokens=10,
            timeout=15,
        )
        return bool(response.choices[0].message.content)
    except Exception as e:
        log.error(f"AI health check failed: {e}")
        return False

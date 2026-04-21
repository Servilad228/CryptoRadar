"""
CryptoRadar — AI-аналитик (DeepSeek через OpenRouter).
Генерирует текстовое резюме и детальный анализ для прошедших монет.
"""

import json
from openai import OpenAI

import config
from logger import log
from models import ScreenResult


_SYSTEM_PROMPT = """Ты — профессиональный криптоаналитик. Тебе присылают данные технического анализа монеты с двух таймфреймов (15m и 1h). Направления на обоих ТФ совпадают.

Твоя задача — дать СТРУКТУРИРОВАННЫЙ ответ:

1. ⚡ СИЛА СИГНАЛА: 🟢 Сильный / 🟡 Умеренный / 🔴 Слабый

2. 📝 КРАТКОЕ РЕЗЮМЕ (3-7 предложений):
   Почему эта монета интересна прямо сейчас, ключевая логика входа, на что обратить внимание.

3. 📊 КЛЮЧЕВЫЕ УРОВНИ:
   - Поддержка: ...
   - Сопротивление: ...

4. ⚖️ RISK/REWARD:
   Оценка соотношения риск/прибыль.

5. ⚠️ РИСКИ (1-2 пункта):
   Основные опасности этой сделки.

Пиши лаконично, используй эмодзи-маркеры. Не давай прямых финансовых советов — только анализ."""


def _build_user_prompt(screen: ScreenResult, candles_json: str) -> str:
    """Формирует пользовательский промпт с данными монеты."""
    signals_15m = "\n".join(
        f"  • {s}" for s in screen.signals_15m if s.direction == screen.direction
    )
    signals_1h = "\n".join(
        f"  • {s}" for s in screen.signals_1h if s.direction == screen.direction
    )

    return f"""Направление: {screen.direction.value}
Монета: {screen.symbol}
Текущая цена: {screen.last_price}

Анализ 15m (score {screen.score_15m}/10):
{signals_15m}

Анализ 1h (score {screen.score_1h}/10):
{signals_1h}

Последние 50 свечей 1h (OHLCV):
{candles_json}"""


def _get_client() -> OpenAI:
    """Создаёт OpenAI-клиент для OpenRouter."""
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )


def analyze(screen: ScreenResult, klines_1h_df) -> tuple[str, str]:
    """
    Отправляет данные монеты в DeepSeek для анализа.
    
    Возвращает (summary, details):
    - summary: первые 3-7 предложений (для caption)
    - details: полный анализ
    
    Raises: Exception при провале после всех retry.
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
            log.info(f"DeepSeek анализ {screen.symbol} (попытка {attempt})...")

            response = client.chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=1500,
                timeout=config.AI_TIMEOUT,
            )

            full_text = response.choices[0].message.content.strip()

            # Разделяем на summary (до первого раздела) и details (всё)
            summary = _extract_summary(full_text)

            log.info(f"DeepSeek анализ {screen.symbol} — готов ({len(full_text)} символов)")
            return summary, full_text

        except Exception as e:
            last_error = e
            log.warning(f"DeepSeek попытка {attempt} для {screen.symbol} провалилась: {e}")

    # Все попытки исчерпаны
    error_msg = f"DeepSeek анализ {screen.symbol} провалился после {config.AI_MAX_RETRIES} попыток: {last_error}"
    log.error(error_msg)
    raise RuntimeError(error_msg)


def _extract_summary(text: str) -> str:
    """
    Извлекает краткое резюме из полного ответа.
    Берёт текст до третьего раздела (📊 КЛЮЧЕВЫЕ УРОВНИ).
    """
    lines = text.split("\n")
    summary_lines = []
    section_count = 0

    for line in lines:
        # Считаем разделы по эмодзи-маркерам
        if any(marker in line for marker in ["📊 КЛЮЧЕВЫЕ", "⚖️ RISK", "⚠️ РИСКИ"]):
            break
        summary_lines.append(line)

    result = "\n".join(summary_lines).strip()
    # Ограничиваем 900 символами (Telegram caption = 1024, нужен запас для заголовка)
    if len(result) > 900:
        result = result[:897] + "..."
    return result


def health_check() -> bool:
    """
    Проверка доступности DeepSeek (для daily self-test).
    Отправляет простой запрос, возвращает True если API отвечает.
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "user", "content": "Ответь одним словом: работаешь?"},
            ],
            max_tokens=10,
            timeout=15,
        )
        return bool(response.choices[0].message.content)
    except Exception as e:
        log.error(f"DeepSeek health check failed: {e}")
        return False

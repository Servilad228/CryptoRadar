"""
CryptoRadar — AI-аналитик (Анализатор через OpenRouter).
Генерирует резюме, извлекает уровни, рассчитывает вероятность.
Использует Instructor + Pydantic для Structured Outputs.
"""

import json
import instructor
from openai import OpenAI

import config
import database
from logger import log
from models import ScreenResult, Direction
from schemas import AnalysisResponse


_SYSTEM_PROMPT = """Ты — профессиональный криптоаналитик. Тебе присылают данные технического анализа монеты с двух таймфреймов (15m и 1h). Направления на обоих ТФ совпадают.

Твоя задача — проанализировать данные и вернуть структурированный ответ.

ВАЖНО: Если суточный объем торгов монеты слишком мал (менее 5 000 000 USDT) или по технической картине это явно неликвидный щиткоин (огромные тени, отсутствие объемов, аномальные пампы), установи флаг is_shitcoin в true.

В summary пиши БЕЗ markdown разметки, только plain text с эмодзи. Конкретно объясни, почему монета пойдёт в указанном направлении, логику входа и на что обратить внимание.

Уровни support и resistance должны быть точными ценами (float). Не давай прямых финансовых советов — только анализ данных."""


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
    Отправляет данные монеты в AI для анализа с использованием Structured Outputs.
    
    Возвращает (summary, details, levels):
    - summary: краткое резюме (для caption)
    - details: полный анализ с рисками
    - levels: {"support": float, "resistance": float}
    
    Raises: RuntimeError при провале после всех retry или если is_shitcoin=True.
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
    
    # Оборачиваем клиент в Instructor для Structured Outputs
    base_client = _get_client()
    client = instructor.from_openai(base_client, mode=instructor.Mode.JSON)

    last_error = None

    for attempt in range(1, config.AI_MAX_RETRIES + 1):
        try:
            log.info(f"AI анализ {screen.symbol} (попытка {attempt})...")

            # Используем Instructor с response_model=AnalysisResponse
            response: AnalysisResponse = client.chat.completions.create(
                model=config.ANALYZER_MODEL,
                response_model=AnalysisResponse,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=2500,
                timeout=config.AI_TIMEOUT,
            )

            # Проверка флага щиткоина
            if response.is_shitcoin:
                raise RuntimeError(
                    f"Щиткоин отсеян нейросетью по объему/паттернам: {screen.symbol}"
                )

            # Формируем полный текст анализа для details
            risks_text = "\n".join(f"- {risk}" for risk in response.risks)
            full_text = f"{response.summary}\n\n⚠️ РИСКИ:\n{risks_text}"
            
            if response.key_insight:
                full_text += f"\n\n💡 {response.key_insight}"

            # Формируем levels dict
            levels = {
                "support": response.support,
                "resistance": response.resistance,
            }

            log.info(
                f"AI анализ {screen.symbol} — готов | "
                f"Support: {response.support} | Resistance: {response.resistance}"
            )
            return response.summary, full_text, levels

        except instructor.exceptions.InstructorRetryException as e:
            last_error = e
            log.warning(
                f"AI попытка {attempt} для {screen.symbol} провалилась (Instructor retry): {e}"
            )
        except RuntimeError:
            # Пробрасываем RuntimeError (щиткоин) дальше
            raise
        except Exception as e:
            last_error = e
            log.warning(f"AI попытка {attempt} для {screen.symbol} провалилась: {e}")

    # Все попытки исчерпаны
    error_msg = (
        f"AI анализ {screen.symbol} провалился после {config.AI_MAX_RETRIES} попыток: "
        f"{last_error}"
    )
    log.error(error_msg)
    raise RuntimeError(error_msg)


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

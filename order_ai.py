"""
CryptoRadar — AI генерация ордеров (ORDER_MODEL — Claude Sonnet).
Создаёт оптимальные параметры ордера, механическая валидация.
Использует Instructor + Pydantic для Structured Outputs.
"""

import instructor
from openai import OpenAI

import config
import database
from logger import log
from models import OrderParams, Direction
from schemas import OrderResponse


_ORDER_SYSTEM_PROMPT = """Ты — профессиональный трейдер. На основе уровней поддержки/сопротивления и технического анализа рассчитай оптимальный ордер.

Твоя задача — вернуть структурированный ответ с параметрами ордера.

ПРАВИЛА:
1. Оцени признаки щиткоина: если монета имеет ограниченную ликвидность, аномальные пампы/тени или суточный объём < 5M USDT - установи флаг is_shitcoin в true.
2. Risk/Reward ДОЛЖЕН быть в коридоре от {rr_min} до {rr_max}
3. Stop-Loss должен быть за ближайшим уровнем (поддержка для LONG, сопротивление для SHORT)
4. Take-Profit — на уровне сопротивления (LONG) или поддержки (SHORT)
5. Размер позиции рассчитывается из целевого профита: qty = target_profit / |TP - entry|
6. Entry должен быть рядом с текущей ценой (±1-2%)

В reasoning кратко объясни логику расстановки уровней (2-3 предложения)."""


def _get_client() -> OpenAI:
    """Создаёт OpenAI-клиент для OpenRouter."""
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )


def generate_order_params(
    symbol: str,
    direction: str,
    current_price: float,
    support: float,
    resistance: float,
    volume_24h: float = 0.0,
    target_profit: float = None,
    rr_min: float = None,
    rr_max: float = None,
) -> OrderParams:
    """
    Claude Sonnet генерирует параметры ордера.
    
    Raises: RuntimeError при провале.
    """
    if target_profit is None:
        target_profit = config.TARGET_PROFIT_USD
    if rr_min is None:
        rr_min = config.RR_MIN
    if rr_max is None:
        rr_max = config.RR_MAX

    system_prompt = _ORDER_SYSTEM_PROMPT.format(rr_min=rr_min, rr_max=rr_max)

    user_prompt = f"""Монета: {symbol}
Направление: {direction}
Текущая цена: {current_price}
Поддержка: {support}
Сопротивление: {resistance}
Суточный объем (24h USDT): {volume_24h}
Целевой профит: ${target_profit}
R/R коридор: {rr_min} — {rr_max}"""

    # Вставляем советы из прошлых сделок
    tips = database.get_tips("order", config.MAX_TIPS_IN_PROMPT)
    if tips:
        tips_text = "\n".join(f"- {t}" for t in tips)
        user_prompt += f"\n\n📋 Советы с прошлых сделок:\n{tips_text}"

    # Оборачиваем клиент в Instructor для Structured Outputs
    base_client = _get_client()
    client = instructor.from_openai(base_client, mode=instructor.Mode.JSON)
    
    last_error = None
    original_user_prompt = user_prompt  # Сохраняем оригинальный промпт для retry

    for attempt in range(1, config.AI_MAX_RETRIES + 1):
        try:
            log.info(f"ORDER_MODEL: генерация ордера {symbol} {direction} (попытка {attempt})")

            # Используем Instructor с response_model=OrderResponse
            response: OrderResponse = client.chat.completions.create(
                model=config.ORDER_MODEL,
                response_model=OrderResponse,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,  # низкая для точности расчётов
                max_tokens=500,
                timeout=config.AI_TIMEOUT,
            )

            # Проверка флага щиткоина
            if response.is_shitcoin:
                raise RuntimeError(
                    f"Отсеяно ORDER_MODEL: обнаружен щиткоин по признакам и объёму: {symbol}"
                )

            # Конвертируем OrderResponse в OrderParams (внутренняя модель)
            params = OrderParams(
                symbol=symbol,
                direction=Direction.LONG if direction == "LONG" else Direction.SHORT,
                entry=response.entry,
                sl=response.sl,
                tp=response.tp,
                qty=response.qty,
                rr_ratio=response.rr_ratio,
                reasoning=response.reasoning,
            )

            # Механическая валидация
            is_valid, errors = validate_order(params, direction, current_price)

            if is_valid:
                log.info(
                    f"ORDER_MODEL: ордер {symbol} валиден | "
                    f"Entry: {params.entry} | SL: {params.sl} | TP: {params.tp} | "
                    f"R/R: {params.rr_ratio:.2f}"
                )
                return params
            else:
                error_text = "; ".join(errors)
                log.warning(f"ORDER_MODEL: ордер {symbol} не прошёл валидацию: {error_text}")

                if attempt < config.AI_MAX_RETRIES:
                    # Retry с feedback об ошибках
                    user_prompt = (
                        f"{original_user_prompt}\n\n"
                        f"⚠️ Предыдущий ответ не прошёл проверку:\n{error_text}\n"
                        f"Исправь параметры."
                    )
                    continue
                else:
                    raise RuntimeError(f"Ордер не прошёл валидацию: {error_text}")

        except instructor.exceptions.InstructorRetryException as e:
            last_error = e
            log.warning(
                f"ORDER_MODEL: попытка {attempt} провалилась (Instructor retry): {e}"
            )
        except RuntimeError:
            # Пробрасываем RuntimeError (щиткоин или валидация) дальше
            raise
        except Exception as e:
            last_error = e
            log.warning(f"ORDER_MODEL: попытка {attempt} провалилась: {e}")

    raise RuntimeError(f"ORDER_MODEL: генерация ордера {symbol} провалилась: {last_error}")


def validate_order(
    params: OrderParams, 
    direction: str, 
    current_price: float,
) -> tuple[bool, list[str]]:
    """
    Механическая проверка параметров ордера БЕЗ LLM.
    
    Returns: (is_valid, list_of_errors)
    """
    errors = []

    # 1. Проверка сторон
    if direction == "LONG":
        if params.sl >= params.entry:
            errors.append(f"LONG: SL ({params.sl}) должен быть ниже Entry ({params.entry})")
        if params.tp <= params.entry:
            errors.append(f"LONG: TP ({params.tp}) должен быть выше Entry ({params.entry})")
    else:
        if params.sl <= params.entry:
            errors.append(f"SHORT: SL ({params.sl}) должен быть выше Entry ({params.entry})")
        if params.tp >= params.entry:
            errors.append(f"SHORT: TP ({params.tp}) должен быть ниже Entry ({params.entry})")

    # 2. Математический R/R
    risk = abs(params.entry - params.sl)
    reward = abs(params.tp - params.entry)

    if risk == 0:
        errors.append("Risk = 0 (SL = Entry)")
    else:
        actual_rr = reward / risk
        # Проверяем, что заявленный R/R совпадает с расчётным (±10%)
        if abs(actual_rr - params.rr_ratio) / max(params.rr_ratio, 0.01) > 0.1:
            errors.append(
                f"R/R не совпадает: заявлено {params.rr_ratio:.2f}, "
                f"расчётно {actual_rr:.2f}"
            )

        # 3. R/R в допустимом коридоре
        if actual_rr < config.RR_MIN:
            errors.append(f"R/R ({actual_rr:.2f}) ниже минимума ({config.RR_MIN})")
        if actual_rr > config.RR_MAX:
            errors.append(f"R/R ({actual_rr:.2f}) выше максимума ({config.RR_MAX})")

    # 4. Entry в пределах ±2% от текущей цены
    if current_price > 0:
        entry_diff_pct = abs(params.entry - current_price) / current_price * 100
        if entry_diff_pct > 2.0:
            errors.append(
                f"Entry ({params.entry}) отклоняется от текущей цены "
                f"({current_price}) на {entry_diff_pct:.1f}% (макс 2%)"
            )

    # 5. Qty положительный
    if params.qty <= 0:
        errors.append(f"Qty ({params.qty}) должен быть положительным")

    # 6. Проверка профита
    if reward > 0 and params.qty > 0:
        expected_profit = params.qty * reward
        target = config.TARGET_PROFIT_USD
        if abs(expected_profit - target) / max(target, 0.01) > 0.3:
            errors.append(
                f"Ожидаемый профит (${expected_profit:.2f}) отклоняется "
                f"от целевого (${target:.2f}) более чем на 30%"
            )

    return len(errors) == 0, errors

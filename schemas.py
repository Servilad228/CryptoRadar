"""
CryptoRadar — Pydantic схемы для Structured Outputs.
Используются с библиотекой Instructor для типобезопасного взаимодействия с LLM.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


# ══════════════════════════════════════════════════════════════
# СХЕМА 1: AI Analyst Response (DeepSeek v3)
# ══════════════════════════════════════════════════════════════

class AnalysisResponse(BaseModel):
    """
    Структурированный ответ от AI Analyst.
    Используется для анализа монеты и определения ключевых уровней.
    """
    
    summary: str = Field(
        description=(
            "Краткое резюме анализа (5-7 предложений). "
            "Конкретно объясни, почему монета пойдёт в указанном направлении, "
            "логику входа и на что обратить внимание. "
            "БЕЗ markdown разметки, только plain text с эмодзи."
        ),
        min_length=100,
        max_length=900,
    )
    
    support: float = Field(
        description=(
            "Ключевой уровень поддержки (точная цена). "
            "Для LONG — это уровень, где можно ставить Stop-Loss. "
            "Для SHORT — это целевой уровень Take-Profit."
        ),
        gt=0,
    )
    
    resistance: float = Field(
        description=(
            "Ключевой уровень сопротивления (точная цена). "
            "Для LONG — это целевой уровень Take-Profit. "
            "Для SHORT — это уровень, где можно ставить Stop-Loss."
        ),
        gt=0,
    )
    
    risks: List[str] = Field(
        description=(
            "Список из 2-3 ключевых рисков для данной сделки. "
            "Каждый риск — это одно предложение без нумерации."
        ),
        min_length=2,
        max_length=3,
    )
    
    is_shitcoin: bool = Field(
        default=False,
        description=(
            "Флаг щиткоина. Установи в true, если монета имеет: "
            "суточный объём < 5M USDT, аномальные пампы/дампы, "
            "огромные тени на свечах, отсутствие ликвидности."
        ),
    )
    
    key_insight: Optional[str] = Field(
        default=None,
        description=(
            "Опциональный ключевой инсайт (1-2 предложения) — "
            "что особенного в этой монете прямо сейчас."
        ),
        max_length=200,
    )
    
    @field_validator("support", "resistance")
    @classmethod
    def validate_positive_prices(cls, v: float) -> float:
        """Проверка, что цены положительные."""
        if v <= 0:
            raise ValueError(f"Цена должна быть положительной, получено: {v}")
        return v
    
    @field_validator("resistance")
    @classmethod
    def validate_levels_order(cls, v: float, info) -> float:
        """Проверка, что resistance > support."""
        if "support" in info.data:
            support = info.data["support"]
            if v <= support:
                raise ValueError(
                    f"Resistance ({v}) должен быть выше Support ({support})"
                )
        return v


# ══════════════════════════════════════════════════════════════
# СХЕМА 2: Order AI Response (Claude Sonnet 3.7)
# ══════════════════════════════════════════════════════════════

class OrderResponse(BaseModel):
    """
    Структурированный ответ от Order AI.
    Используется для генерации параметров ордера с оптимальным R/R.
    """
    
    entry: float = Field(
        description=(
            "Цена входа в позицию. Должна быть близка к текущей цене (±1-2%). "
            "Для LONG — чуть выше поддержки, для SHORT — чуть ниже сопротивления."
        ),
        gt=0,
    )
    
    sl: float = Field(
        description=(
            "Stop-Loss (цена). "
            "Для LONG: SL должен быть НИЖЕ entry (за уровнем поддержки). "
            "Для SHORT: SL должен быть ВЫШЕ entry (за уровнем сопротивления)."
        ),
        gt=0,
    )
    
    tp: float = Field(
        description=(
            "Take-Profit (цена). "
            "Для LONG: TP должен быть ВЫШЕ entry (на уровне сопротивления). "
            "Для SHORT: TP должен быть НИЖЕ entry (на уровне поддержки)."
        ),
        gt=0,
    )
    
    qty: float = Field(
        description=(
            "Количество монет для покупки/продажи. "
            "Рассчитывается из целевого профита: qty = target_profit / |TP - entry|. "
            "Должно быть положительным числом."
        ),
        gt=0,
    )
    
    rr_ratio: float = Field(
        description=(
            "Risk/Reward соотношение (например, 2.0 означает 1:2). "
            "Рассчитывается как: |TP - entry| / |entry - SL|. "
            "Должно быть в диапазоне от 1.5 до 3.0."
        ),
        ge=1.5,
        le=3.0,
    )
    
    reasoning: str = Field(
        description=(
            "Краткое объяснение логики расстановки уровней (2-3 предложения). "
            "Почему именно эти цены для entry/SL/TP, какая логика за R/R."
        ),
        min_length=50,
        max_length=300,
    )
    
    is_shitcoin: bool = Field(
        default=False,
        description=(
            "Флаг щиткоина. Установи в true, если при расчёте ордера "
            "обнаружены признаки неликвидности или аномальных движений."
        ),
    )
    
    @field_validator("entry", "sl", "tp", "qty")
    @classmethod
    def validate_positive_values(cls, v: float) -> float:
        """Проверка положительных значений."""
        if v <= 0:
            raise ValueError(f"Значение должно быть положительным, получено: {v}")
        return v


# ══════════════════════════════════════════════════════════════
# СХЕМА 3: Lesson Analyzer Response (Claude Sonnet 3.7)
# ══════════════════════════════════════════════════════════════

class LessonResponse(BaseModel):
    """
    Структурированный ответ от Lesson Analyzer.
    Используется для разбора закрытых сделок и генерации советов.
    """
    
    analysis: str = Field(
        description=(
            "Полный разбор сделки без ограничения по длине. "
            "Детально опиши что пошло правильно/неправильно, "
            "какие сигналы были учтены/проигнорированы, "
            "можно ли было предвидеть результат."
        ),
        min_length=200,
    )
    
    tip_analyzer: str = Field(
        description=(
            "Совет для AI Analyst (1-2 предложения). "
            "Что первичный анализатор монеты должен учитывать в будущем. "
            "Должен быть УНИКАЛЬНЫМ и НЕ повторять уже данные советы."
        ),
        min_length=30,
        max_length=200,
    )
    
    tip_order: str = Field(
        description=(
            "Совет для Order AI (1-2 предложения). "
            "Что генератор ордеров должен учитывать при расстановке entry/SL/TP. "
            "Должен быть УНИКАЛЬНЫМ и НЕ повторять уже данные советы."
        ),
        min_length=30,
        max_length=200,
    )
    
    key_mistake: str = Field(
        description=(
            "Ключевая ошибка в этой сделке (одно предложение). "
            "Если сделка прибыльная — что можно было сделать ещё лучше. "
            "Если убыточная — главная причина провала."
        ),
        min_length=20,
        max_length=150,
    )
    
    confidence: float = Field(
        description=(
            "Уверенность в анализе от 0.0 до 1.0. "
            "1.0 = абсолютно уверен в выводах, "
            "0.5 = средняя уверенность, "
            "0.0 = низкая уверенность (недостаточно данных)."
        ),
        ge=0.0,
        le=1.0,
    )
    
    improvement_areas: List[str] = Field(
        default_factory=list,
        description=(
            "Опциональный список из 2-3 областей для улучшения. "
            "Каждая область — это короткая фраза (3-5 слов)."
        ),
        max_length=3,
    )
    
    @field_validator("confidence")
    @classmethod
    def validate_confidence_range(cls, v: float) -> float:
        """Проверка диапазона уверенности."""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Confidence должен быть от 0.0 до 1.0, получено: {v}")
        return v

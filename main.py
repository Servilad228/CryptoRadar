"""
CryptoRadar — Главный модуль.
Scheduler (каждый час в :00) + Telegram polling + daily self-test.
"""

import signal
import sys
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from logger import log
import scanner
import screener
import ai_analyst
import chart
import telegram_bot


# ── Основной цикл сканирования ────────────────────────────

def run_scan():
    """
    Полный цикл сканирования:
    1. Получить топ-30
    2. Загрузить свечи
    3. Скрининг
    4. AI-анализ + график → Telegram
    """
    start_time = datetime.now()
    log.info("=" * 60)
    log.info(f"🔭 НАЧАЛО СКАНА — {start_time.strftime('%H:%M:%S %d.%m.%Y')}")
    log.info("=" * 60)

    try:
        # 1. Получаем топ-30 монет по объёмам
        top_coins = scanner.get_top_coins(config.TOP_N_COINS)
        if not top_coins:
            log.error("Не удалось получить список монет с Bybit")
            telegram_bot.send_message("❌ Скан провалился: не удалось получить список монет")
            return

        # 2. Загружаем свечи для всех монет
        coins_data = scanner.scan_all(top_coins)
        if not coins_data:
            log.error("Не удалось загрузить свечи ни для одной монеты")
            telegram_bot.send_message("❌ Скан провалился: не удалось загрузить свечи")
            return

        # 3. Скрининг
        passed = screener.screen_all(coins_data)

        # 4. Для каждой прошедшей → AI + график → Telegram
        passed_symbols = []
        for result in passed:
            try:
                # Находим оригинальные данные монеты
                coin_data = next(
                    (c for c in coins_data if c.symbol == result.symbol), None
                )
                if coin_data is None:
                    continue

                # AI-анализ
                try:
                    summary, details = ai_analyst.analyze(result, coin_data.klines_1h)
                except RuntimeError as e:
                    log.error(f"Пропускаю {result.symbol} — AI недоступен: {e}")
                    continue

                # Генерация графика (используем 1h для читаемости)
                try:
                    chart_bytes = chart.generate(result, coin_data.klines_1h)
                except Exception as e:
                    log.warning(f"График для {result.symbol} не сгенерирован: {e}")
                    chart_bytes = None

                # Отправка в Telegram
                telegram_bot.send_alert(
                    symbol=result.symbol,
                    direction=result.direction.value,
                    score_15m=result.score_15m,
                    score_1h=result.score_1h,
                    summary=summary,
                    details=details,
                    chart_bytes=chart_bytes,
                )
                passed_symbols.append(result.symbol)

            except Exception as e:
                log.error(f"Ошибка обработки {result.symbol}: {e}")
                continue

        # 5. Итоговый отчёт
        telegram_bot.send_status_report(
            total=len(coins_data),
            passed=len(passed_symbols),
            passed_symbols=passed_symbols,
        )
        telegram_bot.update_status(datetime.now(), len(passed_symbols), len(coins_data))

        elapsed = (datetime.now() - start_time).total_seconds()
        log.info(
            f"✅ СКАН ЗАВЕРШЁН за {elapsed:.1f}с | "
            f"Монет: {len(coins_data)} | Прошли: {len(passed_symbols)} | "
            f"Отправлено: {', '.join(passed_symbols) or 'нет'}"
        )

    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА СКАНА: {e}", exc_info=True)
        try:
            telegram_bot.send_message(f"❌ Критическая ошибка скана: {e}")
        except Exception:
            pass


# ── Ежедневный self-test ───────────────────────────────────

def run_selftest():
    """
    Ежедневная проверка работоспособности всех компонентов.
    Не отправляет монеты на полный анализ — только проверяет доступность.
    """
    log.info("🔧 Запуск daily self-test...")
    results = []
    all_ok = True

    # 1. Bybit API — получение тикеров
    try:
        coins = scanner.get_top_coins(5)
        if coins:
            results.append(f"✅ Bybit API: доступен ({len(coins)} тикеров)")
        else:
            results.append("❌ Bybit API: пустой ответ")
            all_ok = False
    except Exception as e:
        results.append(f"❌ Bybit API: {e}")
        all_ok = False

    # 2. Bybit Klines — загрузка свечей
    try:
        if coins:
            test_symbol = coins[0]["symbol"]
            klines = scanner.get_klines(test_symbol, interval="60", limit=50)
            if not klines.empty:
                results.append(f"✅ Bybit Klines: {test_symbol} — {len(klines)} свечей")
            else:
                results.append(f"❌ Bybit Klines: пустой DataFrame для {test_symbol}")
                all_ok = False
    except Exception as e:
        results.append(f"❌ Bybit Klines: {e}")
        all_ok = False

    # 3. Индикаторы — расчёт
    try:
        if coins and not klines.empty:
            from indicators import compute_all
            signals = compute_all(klines)
            results.append(f"✅ Индикаторы: {len(signals)} рассчитано для {test_symbol}")
    except Exception as e:
        results.append(f"❌ Индикаторы: {e}")
        all_ok = False

    # 4. DeepSeek — health check (не полный анализ)
    try:
        if ai_analyst.health_check():
            results.append("✅ DeepSeek (OpenRouter): доступен")
        else:
            results.append("❌ DeepSeek (OpenRouter): не отвечает")
            all_ok = False
    except Exception as e:
        results.append(f"❌ DeepSeek: {e}")
        all_ok = False

    # 5. Chart — генерация тестового графика
    try:
        if coins and not klines.empty:
            from models import ScreenResult, Direction
            test_screen = ScreenResult(
                symbol=test_symbol, last_price=0,
                direction=Direction.LONG, score_15m=0, score_1h=0,
            )
            chart_bytes = chart.generate(test_screen, klines)
            results.append(f"✅ Генерация графика: {len(chart_bytes)} bytes")
    except Exception as e:
        results.append(f"❌ Генерация графика: {e}")
        all_ok = False

    # Итог
    status = "✅ ВСЕ СИСТЕМЫ В НОРМЕ" if all_ok else "⚠️ ЕСТЬ ПРОБЛЕМЫ"
    report = f"{status}\n\n" + "\n".join(results)
    report += f"\n\nВремя: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"

    log.info(f"Self-test завершён: {status}")
    telegram_bot.send_selftest_report(report)


# ── Запуск ─────────────────────────────────────────────────

def main():
    """Точка входа: запуск scheduler + Telegram bot polling."""
    log.info("🚀 CryptoRadar запускается...")

    # Проверяем конфигурацию
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN не установлен!")
        sys.exit(1)
    if not config.TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_CHAT_ID не установлен!")
        sys.exit(1)
    if not config.OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY не установлен!")
        sys.exit(1)

    # ── Scheduler ──
    scheduler = BackgroundScheduler()

    # Скан каждый час в :00
    scheduler.add_job(
        run_scan,
        trigger=CronTrigger(minute=config.SCAN_AT_MINUTE),
        id="hourly_scan",
        name="Hourly Scan",
        misfire_grace_time=300,  # 5 мин допуск
    )

    # Self-test раз в сутки
    scheduler.add_job(
        run_selftest,
        trigger=CronTrigger(hour=config.SELFTEST_HOUR, minute=config.SELFTEST_MINUTE),
        id="daily_selftest",
        name="Daily Self-Test",
        misfire_grace_time=600,
    )

    scheduler.start()
    log.info(
        f"Scheduler запущен: скан каждый час в :{config.SCAN_AT_MINUTE:02d}, "
        f"self-test в {config.SELFTEST_HOUR:02d}:{config.SELFTEST_MINUTE:02d}"
    )

    # ── Telegram Bot ──
    # Устанавливаем коллбэк для /scan
    telegram_bot.set_scan_callback(run_scan)

    app = telegram_bot.start_bot()

    # Graceful shutdown
    def shutdown(sig, frame):
        log.info("Получен сигнал остановки, выключаюсь...")
        scheduler.shutdown(wait=False)
        log.info("CryptoRadar остановлен.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Отправляем стартовое сообщение
    try:
        telegram_bot.send_message(
            f"🚀 CryptoRadar запущен!\n\n"
            f"Скан: каждый час в :00\n"
            f"Self-test: ежедневно в {config.SELFTEST_HOUR:02d}:00\n"
            f"Монет: топ-{config.TOP_N_COINS}\n"
            f"Порог: {config.MIN_SCORE_THRESHOLD}/10\n\n"
            f"Команды: /scan /status"
        )
    except Exception as e:
        log.error(f"Не удалось отправить стартовое сообщение: {e}")

    log.info("Telegram bot polling запущен. Ожидаю команды...")
    # Блокирующий вызов — polling бота
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

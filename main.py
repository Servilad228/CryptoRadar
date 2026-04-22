"""
CryptoRadar — Главный модуль.
Scheduler + Telegram polling + Server Monitor + DB.
"""

import signal
import sys
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import database
from logger import log
import scanner
import screener
import ai_analyst
import chart
import telegram_bot
import scan_cache
import server_monitor
from position_tracker import position_tracker


# ── Основной цикл сканирования ────────────────────────────

def run_scan():
    """Полный цикл: скрининг -> сохранение -> AI-анализ -> Telegram."""
    start_time = datetime.now()
    log.info("=" * 60)
    log.info(f"🔭 НАЧАЛО СКАНА — {start_time.strftime('%H:%M:%S %d.%m.%Y')}")
    log.info("=" * 60)

    try:
        # 1. Получаем монеты
        top_coins = scanner.get_top_coins(config.TOP_N_COINS)
        if not top_coins:
            log.error("Скан провалился: нет монет")
            telegram_bot.send_message("❌ Скан провалился: не удалось получить список монет")
            return

        # 2. Загружаем свечи
        coins_data = scanner.scan_all(top_coins)
        if not coins_data:
            log.error("Скан провалился: нет свечей")
            telegram_bot.send_message("❌ Скан провалился: не удалось загрузить свечи")
            return

        # 3. Скрининг
        passed = screener.screen_all(coins_data)

        # 4. Сохраняем в кэш
        scan_cache.save_scan(passed, len(coins_data))

        # 5. Обработка прошедших монет
        passed_symbols = []
        for result in passed:
            try:
                coin_data = next((c for c in coins_data if c.symbol == result.symbol), None)
                if not coin_data:
                    continue

                # Расчёт вероятности
                prob = ai_analyst.estimate_probability(result)

                # AI-анализ
                try:
                    summary, details, levels = ai_analyst.analyze(result, coin_data.klines_1h)
                except RuntimeError as e:
                    log.error(f"Пропускаю {result.symbol} — AI недоступен: {e}")
                    continue

                # Генерация графика
                try:
                    chart_bytes = chart.generate(result, coin_data.klines_1h, levels=levels)
                except Exception as e:
                    log.warning(f"График для {result.symbol} не сгенерирован: {e}")
                    chart_bytes = None

                # Отправка в Telegram
                telegram_bot.send_alert(
                    symbol=result.symbol,
                    direction=result.direction.value,
                    score_15m=result.score_15m,
                    score_1h=result.score_1h,
                    probability=prob,
                    summary=summary,
                    details=details,
                    chart_bytes=chart_bytes,
                )
                passed_symbols.append(result.symbol)

            except Exception as e:
                log.error(f"Ошибка обработки {result.symbol}: {e}")
                continue

        # Обновление статуса
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
    """Ежедневная проверка работоспособности всех компонентов."""
    log.info("🔧 Запуск daily self-test...")
    results = []
    all_ok = True

    try:
        coins = scanner.get_top_coins(5)
        results.append(f"✅ Bybit API: доступен ({len(coins)} тикеров)")
    except Exception as e:
        results.append(f"❌ Bybit API: {e}")
        all_ok = False

    try:
        if coins:
            test_symbol = coins[0]["symbol"]
            klines = scanner.get_klines(test_symbol, interval="60", limit=50)
            if not klines.empty:
                results.append(f"✅ Bybit Klines {test_symbol} OK")
    except Exception as e:
        results.append(f"❌ Bybit Klines: {e}")
        all_ok = False

    try:
        if ai_analyst.health_check():
            results.append("✅ ANALYZER_MODEL: отвечает")
        else:
            results.append("❌ ANALYZER_MODEL: ошибка")
            all_ok = False
    except Exception as e:
        results.append(f"❌ ANALYZER_MODEL: {e}")
        all_ok = False

    try:
        import pytest
        import os
        tests_dir = os.path.join(os.path.dirname(__file__), "tests")
        # Вызываем pytest программно. Перехватываем вывод (чтобы не засорять терминал)
        # -q для тишины, --tb=short для кратких ошибок
        exit_code = pytest.main(["-q", "--tb=short", tests_dir])
        if exit_code == 0:
            results.append("✅ Unit-Тесты: Пройдены успешно")
        else:
            results.append(f"❌ Unit-Тесты: ОБНАРУЖЕНЫ ОШИБКИ (code: {exit_code})")
            all_ok = False
    except Exception as e:
        results.append(f"❌ Unit-Тесты: Ошибка запуска ({e})")
        all_ok = False

    status = "✅ ВСЕ СИСТЕМЫ В НОРМЕ" if all_ok else "⚠️ ЕСТЬ ПРОБЛЕМЫ"
    report = f"{status}\n\n" + "\n".join(results) + f"\n\nВремя: {datetime.now().strftime('%H:%M:%S')}"
    log.info(f"Self-test завершён: {status}")
    telegram_bot.send_message(f"🔧 Daily Self-Test Report\n\n{report}")


# ── Запуск ─────────────────────────────────────────────────

def main():
    log.info("🚀 CryptoRadar запускается...")

    # 1. Инициализация БД
    database.init_db()

    # 2. Мониторинг сервера
    server_monitor.monitor.start()

    # 3. Position Tracker
    position_tracker.start()

    # 4. Планировщик (Scheduler)
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        run_scan,
        trigger=CronTrigger(minute=config.SCAN_AT_MINUTE),
        id="hourly_scan",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_selftest,
        trigger=CronTrigger(hour=config.SELFTEST_HOUR, minute=config.SELFTEST_MINUTE),
        id="daily_selftest",
        misfire_grace_time=600,
    )

    # Проверка позиций каждые 10 минут (для Demo mode и fallback)
    scheduler.add_job(
        position_tracker.check_positions_rest,
        'interval',
        minutes=config.DEMO_CHECK_INTERVAL_MIN,
        id="demo_position_tracker",
    )

    scheduler.start()
    log.info("Scheduler запущен.")

    # 5. Telegram Bot (polling)
    telegram_bot.set_scan_callback(run_scan)
    app = telegram_bot.start_bot()

    def shutdown(sig, frame):
        log.info("Остановка...")
        scheduler.shutdown(wait=False)
        server_monitor.monitor.stop()
        position_tracker.stop()
        log.info("CryptoRadar остановлен.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        telegram_bot.send_message(f"🚀 CryptoRadar V2.0 запущен!\nКоманды: /menu /scan")
    except Exception:
        pass

    log.info("Telegram бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

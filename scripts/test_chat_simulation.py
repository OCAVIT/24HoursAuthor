"""Симуляция реального чата заказчик ↔ бот (исполнитель).

Тестирует generate_response() с типичными вопросами заказчика.
Использует реальный GPT-4o-mini (будет расход ~$0.01-0.02).
"""

import asyncio
import os
import sys

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chat_ai.responder import generate_response, BANNED_WORDS

# --- Контекст заказа ---
ORDER = {
    "order_description": (
        "Написать курсовую работу на тему 'Анализ финансовой устойчивости предприятия "
        "на примере ООО Ромашка'. Необходимо: введение, 2 главы (теоретическая + практическая), "
        "заключение, список литературы 15-20 источников. Оформление по ГОСТ."
    ),
    "work_type": "Курсовая работа",
    "subject": "Экономика предприятия",
    "deadline": "18 февраля 2026",
    "required_uniqueness": 65,
    "antiplagiat_system": "ETXT",
    "bid_price": 2500,
    "pages_min": 25,
    "pages_max": 30,
    "font_size": 14,
    "line_spacing": 1.5,
}

# --- Сценарии диалога ---
SCENARIOS = [
    # (сообщение заказчика, статус заказа, описание теста)
    ("Здравствуйте, сможете сделать?", "bid_placed", "Приветствие + вопрос о возможности"),
    ("Когда будет готово?", "accepted", "Вопрос о сроках"),
    ("Какая уникальность будет?", "accepted", "Вопрос про уникальность"),
    ("Можно дешевле?", "bid_placed", "Торг по цене"),
    ("Как продвигается работа?", "generating", "Вопрос о прогрессе"),
    ("Нужно добавить таблицы с расчётами в практическую часть", "generating", "Дополнительные требования"),
    ("Вот методичка, посмотрите", "accepted", "Отправка доп. материалов"),
    ("Это точно не ИИ пишет? Текст какой-то странный", "generating", "Подозрение в AI"),
    ("Антиплагиат показал 55%, а нужно 65%", "delivered", "Жалоба на уникальность"),
    ("Всё хорошо, принимаю работу. Спасибо!", "delivered", "Одобрение работы"),
    ("Нужны правки: во введении ошибка в дате, и в списке литературы добавьте 2 источника за 2025 год", "delivered", "Конкретные правки"),
    ("Срочно! Преподаватель сказал добавить главу 3 — рекомендации. Сможете?", "delivered", "Срочная доработка"),
]


def check_banned(text: str) -> list[str]:
    """Проверить ответ на запрещённые слова (целые слова через regex)."""
    import re
    found = []
    lower = text.lower()
    for pattern in BANNED_WORDS:
        if re.search(pattern, lower):
            found.append(pattern)
    return found


async def main():
    print("=" * 70)
    print("  СИМУЛЯЦИЯ ЧАТА: ЗАКАЗЧИК <-> БОТ (ИСПОЛНИТЕЛЬ)")
    print("  Заказ: Курсовая по экономике, 25-30 стр, 65% ETXT, 2500р")
    print("=" * 70)

    history: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    issues = []

    for i, (customer_msg, status, description) in enumerate(SCENARIOS, 1):
        print(f"\n{'─' * 60}")
        print(f"  Тест #{i}: {description}")
        print(f"  Статус заказа: {status}")
        print(f"{'─' * 60}")
        print(f"  ЗАКАЗЧИК: {customer_msg}")

        try:
            response = await generate_response(
                order_description=ORDER["order_description"],
                message_history=history.copy(),
                new_message=customer_msg,
                order_status=status,
                work_type=ORDER["work_type"],
                subject=ORDER["subject"],
                deadline=ORDER["deadline"],
                required_uniqueness=ORDER["required_uniqueness"],
                antiplagiat_system=ORDER["antiplagiat_system"],
                bid_price=ORDER["bid_price"],
                pages_min=ORDER["pages_min"],
                pages_max=ORDER["pages_max"],
                font_size=ORDER["font_size"],
                line_spacing=ORDER["line_spacing"],
            )

            bot_reply = response.text
            total_cost += response.cost_usd
            total_tokens += response.total_tokens

            print(f"  БОТ:      {bot_reply}")
            print(f"  [{response.total_tokens} tok, ${response.cost_usd:.4f}]")

            # Проверки качества
            banned = check_banned(bot_reply)
            if banned:
                issues.append(f"#{i}: Запрещённые слова: {banned}")
                print(f"  ⚠ ПРОБЛЕМА: запрещённые слова: {banned}")

            sentences = [s.strip() for s in bot_reply.replace("!", ".").replace("?", ".").split(".") if s.strip()]
            if len(sentences) > 5:
                issues.append(f"#{i}: Слишком длинный ответ ({len(sentences)} предложений)")
                print(f"  ⚠ ПРОБЛЕМА: слишком длинный ответ ({len(sentences)} предл.)")

            if len(bot_reply) < 5:
                issues.append(f"#{i}: Слишком короткий ответ")
                print(f"  ⚠ ПРОБЛЕМА: слишком короткий ответ")

            # Добавляем в историю
            history.append({"role": "user", "content": customer_msg})
            history.append({"role": "assistant", "content": bot_reply})

        except Exception as e:
            print(f"  ✗ ОШИБКА: {e}")
            issues.append(f"#{i}: Ошибка: {e}")

    # --- Итоги ---
    print(f"\n{'=' * 70}")
    print("  ИТОГИ СИМУЛЯЦИИ")
    print(f"{'=' * 70}")
    print(f"  Всего тестов:   {len(SCENARIOS)}")
    print(f"  Токенов:        {total_tokens:,}")
    print(f"  Стоимость:      ${total_cost:.4f}")
    print(f"  Проблем:        {len(issues)}")

    if issues:
        print(f"\n  ПРОБЛЕМЫ:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print(f"\n  Все тесты пройдены без проблем!")

    print()


if __name__ == "__main__":
    asyncio.run(main())

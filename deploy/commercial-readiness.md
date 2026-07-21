# Коммерческий запуск All As Planned

Платежи намеренно закрыты fail-safe проверкой. Наличие ключей ЮKassa само по себе
не включает оплату.

## Обязательные настройки

В production `.env` должны быть заполнены:

```dotenv
YT_LOADER_LEGAL_SELLER_NAME=
YT_LOADER_LEGAL_SELLER_INN=
YT_LOADER_LEGAL_SELLER_ADDRESS=
YT_LOADER_LEGAL_SUPPORT_EMAIL=support@allasplanned.ru
YT_LOADER_LEGAL_VERSION=2026-07-17
YT_LOADER_LEGAL_DOCUMENTS_APPROVED=false
YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE=true

YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_API_URL=https://api.yookassa.ru/v3
YOOKASSA_WEBHOOK_ENFORCE_IP=true
YT_LOADER_PUBLIC_BASE_URL=https://allasplanned.ru
YT_LOADER_ENABLE_PAYMENTS=false
```

Сначала владелец или привлечённый юрист проверяет оферту, политику
конфиденциальности, согласие на обработку данных, возвраты и хранение. Только
после проверки устанавливаются:

```dotenv
YT_LOADER_LEGAL_DOCUMENTS_APPROVED=true
YT_LOADER_ENABLE_PAYMENTS=true
```

Проверка состояния:

```bash
curl -fsS https://allasplanned.ru/api/legal/config
```

После входа в аккаунт:

```bash
curl -fsS -b cookies.txt https://allasplanned.ru/api/payments/config
```

Ожидается `complete=true` для legal config и `enabled=true` для payment config.

## Матрица тестового магазина ЮKassa

Перед боевым магазином последовательно проверить:

1. успешную оплату и единственное начисление кредитов;
2. отменённую оплату без начисления;
3. двойное нажатие на оплату без второго платежа;
4. повторный webhook без второго начисления;
5. webhook с неверной суммой или владельцем платежа;
6. возврат, отзыв кредитов и завершение подписки;
7. временную ошибку возврата и повтор с тем же idempotency key;
8. отменённый возврат и восстановление удержанных кредитов;
9. отключение и возобновление автопродления;
10. успешное продление и окончание подписки при неуспешном списании.
11. разовую покупку каждого пакета кредитов без сохранения способа оплаты;
12. повторный webhook пакета без повторного начисления и полный возврат пакета.

Каждую операцию сверить одновременно в ЮKassa, пользовательском биллинге и
админском журнале действий. Боевые ключи не записывать в Git, логи или
скриншоты.

## Решение о запуске

Платежи можно включать, только если:

- документы показывают реальные реквизиты владельца;
- регистрация требует согласия и сохраняет его версию;
- тестовый магазин прошёл всю матрицу;
- webhook доступен по HTTPS и ограничен доверенными адресами;
- поддержка умеет найти пользователя, платёж, задание и оформить возврат;
- создан свежий backup и проверено восстановление.

Тексты в репозитории являются технической основой публикации, а не заключением
о соответствии конкретной организационно-правовой форме владельца.

Пошаговая настройка магазина и webhook: [payment-provider-setup.md](payment-provider-setup.md).
Экономические допущения тарифов: [pricing-economics.md](pricing-economics.md).

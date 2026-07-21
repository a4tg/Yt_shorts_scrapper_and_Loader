# Подключение ЮKassa к All As Planned

Код подписок, разовых пакетов, webhook, возвратов и автопродления уже входит в
приложение. До получения реквизитов магазина платежи остаются выключенными.

## 1. Настройка магазина

Создайте магазин ЮKassa на юридическое лицо, ИП или другой допустимый для вашего
случая статус. В кабинете укажите webhook:

```text
https://allasplanned.ru/api/payments/yookassa/webhook
```

Подпишите события `payment.succeeded`, `payment.canceled` и используйте HTTPS.
Секретный ключ показывается только владельцу магазина; не отправляйте его в чат,
issue, Git или скриншот.

## 2. Production `.env`

```dotenv
YOOKASSA_SHOP_ID=идентификатор_магазина
YOOKASSA_SECRET_KEY=секретный_ключ
YOOKASSA_API_URL=https://api.yookassa.ru/v3
YOOKASSA_WEBHOOK_ENFORCE_IP=true
YOOKASSA_WEBHOOK_NETWORKS=
YOOKASSA_CONFIRMATION_HOSTS=yoomoney.ru,yookassa.ru

# Включайте чеки только в соответствии с выбранной схемой фискализации.
YOOKASSA_RECEIPT_ENABLED=false
YOOKASSA_VAT_CODE=1

YT_LOADER_PUBLIC_BASE_URL=https://allasplanned.ru
YT_LOADER_ENABLE_PAYMENTS=false
```

Пустой `YOOKASSA_WEBHOOK_NETWORKS` использует встроенный список официальных
сетей ЮKassa. Не отключайте проверку IP в production.

Если чеки формирует ЮKassa, установите `YOOKASSA_RECEIPT_ENABLED=true` и
согласуйте `YOOKASSA_VAT_CODE` с бухгалтером. Если чеки формирует внешняя касса,
не включайте дублирующий receipt без проверки схемы.

## 3. Юридический предохранитель

Заполните реальные данные продавца, проверьте опубликованные документы и только
после этого переключите оба флага:

```dotenv
YT_LOADER_LEGAL_DOCUMENTS_APPROVED=true
YT_LOADER_ENABLE_PAYMENTS=true
```

Приложение не покажет оплату, если отсутствуют ключи, HTTPS URL или подтверждение
юридических документов.

## 4. Проверка до сборки

```bash
cd /opt/yt-loader
python3 deploy/production_preflight.py --env-file .env --commercial
docker compose config --quiet
docker compose up -d --build
curl -fsS https://allasplanned.ru/api/payments/config
```

В последнем ответе ожидается `"enabled":true`.

## 5. Обязательный ручной тест

Сначала используйте тестовый магазин. Купите Creator, отмените один платёж,
повторите webhook, отключите автопродление и оформите возврат из админки. Затем
отдельно купите пакет 100 кредитов и убедитесь, что он не создал подписку и не
сохранил карту. Полная матрица находится в `commercial-readiness.md`.


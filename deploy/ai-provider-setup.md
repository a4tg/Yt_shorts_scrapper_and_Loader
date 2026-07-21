# Подключение AI-провайдера

Production-профиль рассчитан на один ключ AI Tunnel. Он закрывает текст,
изображения, транскрибацию и AI-нарезку через OpenAI-совместимый API.

Добавьте в `.env` только секретный ключ; остальные значения уже есть в
`.env.example`:

```dotenv
AAP_AI_PROVIDER=aitunnel
AAP_AI_API_KEY=ВСТАВЬТЕ_КЛЮЧ_AI_TUNNEL
AAP_AI_BASE_URL=https://api.aitunnel.ru/v1
AAP_AI_API_MODE=auto
AAP_AI_FEATURES=text,image,transcription,clips
AAP_AI_TEXT_MODEL=deepseek-v4-flash
AAP_AI_PREMIUM_TEXT_MODEL=gpt-5.4-mini
AAP_AI_IMAGE_MODEL=gpt-image-2
AAP_AI_IMAGE_QUALITY=low
AAP_AI_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE=auto
AAP_AI_MONTHLY_BUDGET_RUB=5000
```

Не добавляйте `OPENAI_API_KEY` одновременно с `AAP_AI_API_KEY`: новые переменные
имеют приоритет, а два ключа усложняют диагностику.

После изменения конфигурации:

```bash
cd /opt/yt-loader
docker compose up -d --force-recreate yt-loader
docker compose exec -T yt-loader \
  python -c "from ai_service import check_ai_connection; print(check_ai_connection())"
```

Проверка выполняет один минимальный текстовый запрос. Она возвращает провайдера,
модель, режим API и задержку, но никогда не печатает ключ или баланс провайдера.
Изображение, транскрибацию и нарезку нужно проверить вручную по одному разу перед
открытием регистрации.

Проверка конфигурации без сетевого запроса:

```bash
python deploy/production_preflight.py --env-file .env
```

Фактическая заявленная провайдером стоимость AI-заданий сохраняется во внутренней
записи задания. Сводка текущего месяца доступна администратору в
`GET /api/admin/overview` → `ai_usage_month`; API-ключ и баланс провайдера туда не
попадают.

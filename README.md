# SockLock Creative Engine

## Що це

Генератор зображень для маркетологів і дизайнерів: опишіть, що потрібно, і за хвилину отримайте кілька варіантів, які можна одразу завантажити. Відео заплановане (див. розділ «Відео»).

## Швидкий старт

1. Скопіюйте `.env.example` у `.env` і вставте ключ: `OPENAI_API_KEY=...` (ключ створюється на https://platform.openai.com/api-keys). Якщо хочете новішу модель, наприклад GPT Image 2.6, впишіть її в `OPENAI_IMAGE_MODEL`; точний ідентифікатор покаже `python3 -m engine models --filter image`.
2. Запустіть: `python3 -m engine web`
3. Відкрийте http://127.0.0.1:8780

Потрібен лише Python 3, нічого встановлювати не треба.

## Як користуватися

Опишіть зображення, оберіть скільки штук потрібно, формат і стиль, натисніть «Згенерувати». Картинки з'являються паралельно. Під кожною є «Ще 4 схожі»: вона робить чотири варіації саме цієї картинки. Позначте потрібні, щоб завантажити їх одним ZIP-файлом або видалити. Усе згенероване зберігається в `library/gallery/` і лишається в галереї після перезапуску.

## Скільки це коштує

Кожне зображення оплачується з вашого ключа OpenAI. Ціни змінюються, актуальні дивіться на https://developers.openai.com/api/docs/pricing. Якщо вказати `OPENAI_IMAGE_PRICE_USD` (ціна одного зображення), сторінка покаже орієнтовну суму перед генерацією.

## Налаштування

Змінні читаються з `.env` у корені проєкту (або з середовища).

| Змінна | Що означає |
| --- | --- |
| `OPENAI_API_KEY` | Ваш ключ OpenAI (обов'язково, крім демо) |
| `OPENAI_IMAGE_MODEL` | Модель зображень (за замовчуванням `gpt-image-2`) |
| `OPENAI_IMAGE_QUALITY` | Якість: `low`, `medium`, `high` (за замовчуванням `medium`) |
| `OPENAI_IMAGE_SIZE` | Розмір за замовчуванням лише для старого сценарного режиму (`/workflow`); на головній сторінці розмір задає вибраний формат |
| `IMAGE_WORKERS` | Скільки зображень генерується одночасно (за замовчуванням 4) |
| `OPENAI_IMAGE_PRICE_USD` | Ціна одного зображення для орієнтовної суми (необов'язково) |
| `OPENAI_TEXT_MODEL` | Текстова модель, потрібна лише для сторінки сценаріїв |
| `DEMO_IMAGE_DELAY` | Штучна затримка в демо-режимі, секунди (необов'язково) |

## Демо без ключа

    python3 -m engine --backend demo web

Замість справжніх зображень віддається заготовка `samples/demo/frame.png`, нічого не витрачається.

## Відео

Заплановано. Відеопровайдер підключається через `register_provider(назва, "video", фабрика)`, наприклад HeyGen: `register_provider("heygen", "video", factory)`. Це лише точка розширення, готової реалізації немає.

## Обмеження

- Модель може проігнорувати прохання «без тексту», тож перевіряйте результат на зайві написи й логотипи.
- Помилки 429 і вичерпаної квоти показуються як є; автоматичних повторів немає, бо кожен повтор це гроші.
- Текстові запити виконуються по одному, зображення генеруються паралельно.
- Ніколи не передавайте файл `.env` нікому і не додавайте його в git.
- Приклади в `samples/` синтетичні.

## Сценарії (для домашки)

Окрема сторінка http://127.0.0.1:8780/workflow та команди CLI для Homework #3 (MarTech): прискорити оригінали, масштабувати модифікації, локалізувати креативи. Працює з тексту й файлів, без доступу до інфраструктури SockLock. Потрібен `OPENAI_API_KEY` і `OPENAI_TEXT_MODEL`.

Бекенд задає `--backend openai|demo` (або змінна `ENGINE_BACKEND`); за замовчуванням `openai`. Список моделей, доступних вашому ключу: `python3 -m engine models [--filter текст]`.

    python3 -m engine ping
    python3 -m engine originals pains samples/pains.txt          # зберігає out/pains.json
    python3 -m engine originals concepts --pain 1                 # бере біль №1 зі збереженого, зберігає out/concepts.json
    python3 -m engine originals build --concept 1 --status winner # бере збережені біль і концепт, 1 виклик моделі
    python3 -m engine modify variants o001 --instruction "5 hooks, curiosity angle" --count 5
    python3 -m engine modify localize o001 --market de --market fr
    python3 -m engine modify compose --pain "Matching socks wastes time" --reuse DEMO=o001 --reuse CTA=o001 --new HOOK --new PAIN
    python3 -m engine library list [--status winner]
    python3 -m engine library show o001
    python3 -m engine library status o001 winner
    python3 -m engine images shot o001 1                          # кадр №1 зі shot list
    python3 -m engine images variant o001 1                       # ключовий візуал варіації №1
    python3 -m engine images list o001
    python3 -m engine visuals                                     # чи доступний генератор

Без ключа працює демо: `python3 -m engine --backend demo originals pains samples/pains.txt` (аргументи за замовчуванням: `--count 5`, `--pain 1`, `--concept 1`, `--new HOOK --new PAIN`).

Сторінка `/workflow` має дві вкладки:

1. **Оригінали**: текст із болями, болі з дослівними цитатами (вигадані цитати відкидаються), концепти, скрипт по блоках і shot list, бібліотека (`library/originals.json`).
2. **Модифікації**: оригінал із бібліотеки (за замовчуванням `winner`), варіації хуків/фраз/тексту, локалізація (DE, FR, ES, IT, PL) із позначками «потрібна вичитка», збірка з готових блоків і частка їх повторного використання.

Результати пишуться в `out/`; зображення кадрів у `library/images/<id>/`.

Поради:

- `originals build` за замовчуванням ставить статус `draft`, а вкладка 2 показує лише `winner`. Додайте `--status winner` або виконайте `library status ID winner`.
- За один `build` створюється один концепт (один виклик моделі).
- Позначки про задовгий текст на екрані (ONSCREEN) трапляються часто: це підказка для вичитки, а не збій.
- `compose` створює лише англійський текст; для інших ринків локалізуйте результат окремо.
- CLI і вебсторінка не повинні викликати модель одночасно: міжпроцесного замка немає (в межах однієї вебсторінки текстові запити йдуть по черзі, зайнятий сервер відповідає 409).
- Попередній бекенд codex видалено; доступні лише `openai` і `demo`.

## Тести

    python3 -m unittest discover -s tests -t . -v          # офлайн
    RUN_OPENAI=1 python3 -m unittest tests.test_live -v     # справжній OpenAI API (платно, ключ береться з .env)

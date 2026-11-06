# SockLock Creative Engine

Самостійна утиліта для Homework #3 (MarTech): прискорює оригінали, масштабує модифікації та локалізує креативи. Працює з тексту й файлів, без доступу до інфраструктури SockLock.

## Запуск

Потрібні Python 3 і (для справжньої моделі) Codex CLI з входом через ChatGPT:

    /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex login status   # Logged in using ChatGPT

Інший шлях до Codex: змінна `CODEX_BIN`.

Без підписки є демо-режим із записаними відповідями (лише аргументи за замовчуванням: `--count 5`, `--pain 1`, `--concept 1`, `--new HOOK --new PAIN`):

    python3 -m engine --backend demo web          # вебсторінка: http://127.0.0.1:8780
    python3 -m engine --backend demo originals pains samples/pains.txt
    python3 -m engine --backend demo originals concepts --pain 1
    python3 -m engine --backend demo originals build --concept 1

Справжня модель (підписка ChatGPT, API-ключі навмисно прибираються з оточення):

    python3 -m engine ping
    python3 -m engine originals pains samples/pains.txt          # зберігає out/pains.json
    python3 -m engine originals concepts --pain 1                 # бере біль №1 зі збереженого, зберігає out/concepts.json
    python3 -m engine originals build --concept 1 --status winner # бере збережені біль і концепт, 1 виклик моделі
    python3 -m engine modify variants o001 --instruction "5 hooks, curiosity angle" --count 5
    python3 -m engine modify localize o001 --market de --market fr
    python3 -m engine modify compose --pain "Matching socks wastes time" --reuse DEMO=o001 --reuse CTA=o001 --new HOOK --new PAIN
    python3 -m engine web

Бекенд за замовчуванням задає змінна `ENGINE_BACKEND`; допустимі значення: `codex`, `demo`.

Інші команди:

    python3 -m engine library list [--status winner]     # список оригіналів
    python3 -m engine library show o001                   # скрипт і shot list
    python3 -m engine library status o001 winner          # змінити статус
    python3 -m engine visuals                              # чи доступний генератор зображень

Вебсторінка за замовчуванням слухає порт 8780 (`--port N`, якщо він зайнятий).

## Дві вкладки

1. **Оригінали**: текст із болями → болі з дослівними цитатами (вигадані цитати відкидаються) → концепти → скрипт по блоках + shot list → бібліотека (`library/originals.json`).
2. **Модифікації**: оригінал із бібліотеки (за замовчуванням `winner`) → варіації хуків/фраз/тексту на екрані → локалізація (DE, FR, ES, IT, PL) із позначками «потрібна вичитка» → збірка з готових блоків і частка їх повторного використання.

Результати пишуться в `out/`.

## Фото

Що робить: у вкладці 1 малює кадри зі shot list («Кадр», «Усі кадри»), у вкладці 2 малює ключовий візуал для варіації хука. Файли лежать у `library/images/<id>/` (git-ігнорується): `shot-N.png` і `variant-<8 символів sha1 хука>.png`.

Команди:

    python3 -m engine images shot o001 1        # кадр №1 зі shot list
    python3 -m engine images variant o001 1     # ключовий візуал варіації №1 (після modify variants)
    python3 -m engine images list o001          # які зображення вже є
    python3 -m engine visuals                   # чи доступний генератор

Як працює: через Codex на підписці ChatGPT (інструмент зображень). Приблизно хвилина на зображення; формат 9:16 лише просимо в промпті, керування розміром немає; кожне зображення витрачає ліміт підписки; запити по одному; автоматичного повтору немає; уже згенеровані зображення не перегенеровуються (повертається кеш). Стиль задає поле `style` у `samples/glossary.json`.

Демо-режим (`--backend demo`) віддає `samples/demo/frame.png` замість справжнього зображення.

Розширення: генератори зареєстровані в реєстрі провайдерів. Відео через HeyGen підключається як `register_provider("heygen", "video", factory)`; це не реалізовано, орієнтовна вартість ≈ $1,1 за відео на мову, починаючи з кроку 2.

Поза MVP: редагування справжніх фото, керування розміром, відео, Nano Banana (вхід через Google для Gemini CLI відхиляється для особистих акаунтів).

## Поради й нюанси

- `originals build` за замовчуванням ставить статус `draft`, а вкладка 2 у вебсторінці показує лише `winner`. Додайте `--status winner` або виконайте `library status ID winner`.
- За один `build` створюється один концепт (один виклик моделі).
- Позначки про задовгий текст на екрані (ONSCREEN) трапляються часто: це підказка для вичитки, а не збій.
- Збірка (`compose`) створює лише англійський текст, параметра ринку немає; для інших ринків локалізуйте результат окремо.
- CLI і вебсторінка не повинні викликати модель одночасно: міжпроцесного замка немає (в межах однієї вебсторінки запити по черзі, зайнятий сервер відповідає 409).

## Обмеження

- Одне завдання за раз. Після тайм-ауту, збою входу чи помилки процесу повторного виклику НЕМАЄ (це витрачає ліміти підписки). Єдиний повторний запит: один, якщо відповідь не є коректним JSON або не проходить перевірку, тобто до 2 викликів моделі на крок.
- Підписка має власні ліміти, система їх не обходить.
- Фото генеруються через Codex (див. розділ «Фото»); Gemini / Nano Banana не підключено: вхід через Google для Gemini CLI не підтримується для особистих акаунтів.
- Приклади в `samples/` синтетичні.

## Тести

    python3 -m unittest discover -s tests -t . -v          # офлайн
    RUN_CODEX=1 python3 -m unittest tests.test_live -v      # справжній Codex (текст і фото, витрачає ліміт підписки)

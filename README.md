# SockLock Creative Engine

Самостійна утиліта для Homework #3 (MarTech): прискорює оригінали, масштабує модифікації та локалізує креативи. Працює з тексту й файлів, без доступу до інфраструктури SockLock.

## Запуск

Потрібні Python 3 і (для справжньої моделі) Codex CLI з входом через ChatGPT:

    /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex login status   # Logged in using ChatGPT

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

Інший шлях до Codex: змінна `CODEX_BIN`. Бекенд за замовчуванням: `ENGINE_BACKEND`.

## Дві вкладки

1. **Оригінали**: текст із болями → болі з дослівними цитатами (вигадані цитати відкидаються) → концепти → скрипт по блоках + shot list → бібліотека (`library/originals.json`).
2. **Модифікації**: оригінал із бібліотеки (за замовчуванням `winner`) → варіації хуків/фраз/тексту на екрані → локалізація (DE, FR, ES, IT, PL) із позначками «потрібна вичитка» → збірка з готових блоків і частка їх повторного використання.

Результати пишуться в `out/`.

## Обмеження

- Одне завдання за раз; без автоматичних повторів після збою (це витрачає ліміти підписки).
- Підписка має власні ліміти, система їх не обходить.
- Візуальні чернетки (Gemini / Nano Banana) вимкнені: вхід через Google для Gemini CLI не підтримується для особистих акаунтів.
- Приклади в `samples/` синтетичні.

## Тести

    python3 -m unittest discover -s tests -t . -v          # офлайн
    RUN_CODEX=1 python3 -m unittest tests.test_live -v      # справжній Codex

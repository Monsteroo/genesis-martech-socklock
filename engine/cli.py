import argparse
import json
import sys
from pathlib import Path

from engine import visuals
from engine.library import STATUSES, LibraryError, render_script
from engine.llm import LLMError, get_backend
from engine.localize import MARKETS
from engine.service import Engine

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _pick(items, n, what):
    if not 1 <= n <= len(items):
        raise LibraryError(f"Немає {what} №{n} (доступно: 1–{len(items)}).")
    return items[n - 1]


def cmd_ping(a, e):
    emit({"answer": e.llm.run("### TASK: ping\nReply with exactly one word: ok")})


def cmd_pains(a, e):
    emit(e.pains(_read(a.file)))


def cmd_concepts(a, e):
    pain = _pick(e.saved_pains()["pains"], a.pain, "болю")
    emit(e.concepts(pain))


def cmd_build(a, e):
    saved = e.saved_concepts()
    concept = _pick(saved["concepts"], a.concept, "концепту")
    emit(e.build_original(saved["pain"], concept, a.status))


def cmd_list(a, e):
    emit(e.library.find(a.status))


def cmd_show(a, e):
    r = e.library.get(a.id)
    print(f"{r['id']} · {r['title']} [{r['status']}]\nБіль: {r['pain']}\n\n{render_script(r['blocks'])}\n\nShot list:")
    for i, shot in enumerate(r["shot_list"], 1):
        print(f"  {i}. {shot}")


def cmd_status(a, e):
    emit(e.library.set_status(a.id, a.status))


def cmd_variants(a, e):
    emit(e.variants(a.id, a.instruction, a.count))


def cmd_localize(a, e):
    emit(e.localize(a.id, a.market))


def cmd_compose(a, e):
    reuse = {}
    for item in a.reuse or []:
        if "=" not in item:
            raise LibraryError(f"--reuse очікує формат TYPE=ID, отримано: {item}")
        btype, oid = item.split("=", 1)
        reuse[btype.strip().upper()] = oid.strip()
    emit(e.compose(a.pain, reuse, [t.upper() for t in a.new]))


def cmd_visuals(a, e):
    ok, reason = visuals.available()
    print("Візуальні чернетки доступні." if ok else f"Візуальні чернетки недоступно: {reason}")


def cmd_web(a, e):
    from engine.web import make_server

    try:
        server = make_server(e, a.port)
    except OSError as exc:
        raise LibraryError(f"Порт {a.port} зайнятий. Вкажіть інший: --port N.") from exc
    print(f"Відкрийте http://127.0.0.1:{server.server_port} (Ctrl-C щоб зупинити)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser():
    p = argparse.ArgumentParser(prog="engine", description="SockLock Creative Engine")
    p.add_argument("--backend", choices=["codex", "demo"], default=None,
                   help="бекенд LLM (за замовчуванням codex або змінна ENGINE_BACKEND)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="перевірити зв'язок з моделлю").set_defaults(func=cmd_ping)

    orig = sub.add_parser("originals", help="вкладка 1: оригінали").add_subparsers(dest="sub", required=True)
    s = orig.add_parser("pains")
    s.add_argument("file")
    s.set_defaults(func=cmd_pains)
    s = orig.add_parser("concepts")
    s.add_argument("--pain", type=int, required=True)
    s.set_defaults(func=cmd_concepts)
    s = orig.add_parser("build")
    s.add_argument("--concept", type=int, required=True)
    s.add_argument("--status", default="draft", choices=STATUSES)
    s.set_defaults(func=cmd_build)

    lib = sub.add_parser("library", help="бібліотека оригіналів").add_subparsers(dest="sub", required=True)
    s = lib.add_parser("list")
    s.add_argument("--status")
    s.set_defaults(func=cmd_list)
    s = lib.add_parser("show")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)
    s = lib.add_parser("status")
    s.add_argument("id")
    s.add_argument("status")
    s.set_defaults(func=cmd_status)

    mod = sub.add_parser("modify", help="вкладка 2: модифікації").add_subparsers(dest="sub", required=True)
    s = mod.add_parser("variants")
    s.add_argument("id")
    s.add_argument("--instruction", required=True)
    s.add_argument("--count", type=int, default=5)
    s.set_defaults(func=cmd_variants)
    s = mod.add_parser("localize")
    s.add_argument("id")
    s.add_argument("--market", action="append", required=True, choices=sorted(MARKETS))
    s.set_defaults(func=cmd_localize)
    s = mod.add_parser("compose")
    s.add_argument("--pain", required=True)
    s.add_argument("--reuse", action="append", help="TYPE=ID, напр. DEMO=o001")
    s.add_argument("--new", action="append", required=True, help="тип блоку, який написати заново")
    s.set_defaults(func=cmd_compose)

    sub.add_parser("visuals", help="чи доступні візуальні чернетки").set_defaults(func=cmd_visuals)
    s = sub.add_parser("web", help="локальна вебсторінка")
    s.add_argument("--port", type=int, default=8780)
    s.set_defaults(func=cmd_web)
    return p


def main(argv=None, llm=None, root=None):
    args = build_parser().parse_args(argv)
    root = Path(root) if root else DEFAULT_ROOT
    try:
        if args.func is cmd_visuals:
            args.func(args, None)
            return 0
        backend = llm or get_backend(args.backend)
        args.func(args, Engine(root, backend))
        return 0
    except (LLMError, LibraryError, OSError, ValueError) as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Перервано.", file=sys.stderr)
        return 130

import argparse
import sys
from pathlib import Path

from universal_search import __version__
from universal_search.appconfig import AppPaths
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


def main() -> None:
    # Windows consoles default to a legacy code page; never crash while printing results.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="universal-search")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    # One index, one location: the default database lives in the per-user data
    # directory (never the current or install directory) so the CLI, the GUI
    # and the background worker always see the same index. --database
    # overrides it for tests and tooling.
    default_database = AppPaths.discover().database
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("root", type=Path)
    index.add_argument("--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    index.add_argument(
        "--onedrive-download-mb",
        type=float,
        default=0.0,
        help="explicitly download cloud-only OneDrive files up to this size (0 = never)",
    )
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    search.add_argument("--limit", type=int, default=20)
    search.add_argument(
        "--context", default=None,
        help="context name to apply for this query (overrides the active one)",
    )
    search.add_argument(
        "--explain", action="store_true", help="show the scoring breakdown"
    )
    search.add_argument(
        "--source", choices=("local", "onedrive"), default=None,
        help="only results from this source",
    )
    search.add_argument(
        "--type", dest="doc_type", default=None,
        help="only results of this type, e.g. pdf",
    )
    sub.add_parser("gui", help="launch the desktop search window")
    onedrive = sub.add_parser(
        "onedrive", help="show detected OneDrive roots and file availability"
    )
    onedrive.add_argument(
        "--root", type=Path, default=None, help="scan only this root"
    )

    # -- personal contexts ------------------------------------------------------
    context = sub.add_parser(
        "context", help="personal contexts (Universidad, Trabajo, ...)"
    )
    context_sub = context.add_subparsers(dest="context_command", required=True)
    context_sub.add_parser("list", help="list defined contexts")
    context_add = context_sub.add_parser("add", help="define or extend a context")
    context_add.add_argument("name")
    context_add.add_argument(
        "--root", action="append", default=[],
        help="indexed root of the context (repeatable)",
    )
    context_add.add_argument(
        "--subject", action="append", default=[],
        help="subject/category such as a university subject (repeatable)",
    )
    context_add.add_argument(
        "--type", dest="doc_types", action="append", default=[],
        help="preferred document type, e.g. pdf (repeatable)",
    )
    context_add.add_argument(
        "--source", dest="sources", action="append", default=[],
        help="preferred source: local or onedrive (repeatable)",
    )
    context_add.add_argument(
        "--recency-days", type=int, default=None,
        help="prefer documents modified within the last N days",
    )
    context_remove = context_sub.add_parser("remove", help="delete a context")
    context_remove.add_argument("name")
    context_use = context_sub.add_parser(
        "use", help="activate a context for searches ('none' clears it)"
    )
    context_use.add_argument("name")
    context_relate = context_sub.add_parser(
        "relate", help="define related terms to improve recall"
    )
    context_relate.add_argument("name")
    context_relate.add_argument("term")
    context_relate.add_argument("synonyms", nargs="+")

    # -- local usage learning (privacy: local-only, off by default) -------------
    usage = sub.add_parser(
        "usage", help="local usage learning (local-only, disabled by default)"
    )
    usage_sub = usage.add_subparsers(dest="usage_command", required=True)
    usage_sub.add_parser("on", help="enable local usage learning")
    usage_sub.add_parser("off", help="disable local usage learning")
    usage_show = usage_sub.add_parser("show", help="inspect recorded signals")
    usage_show.add_argument("--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    usage_show.add_argument("--limit", type=int, default=20)
    usage_clear = usage_sub.add_parser(
        "clear", help="delete every recorded signal"
    )
    usage_clear.add_argument("--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )

    # -- global shortcut ---------------------------------------------------------
    hotkey = sub.add_parser(
        "hotkey", help="global shortcut that opens the search window"
    )
    hotkey_sub = hotkey.add_subparsers(dest="hotkey_command", required=True)
    hotkey_sub.add_parser("show", help="show the current shortcut and state")
    hotkey_set = hotkey_sub.add_parser(
        "set", help="change the shortcut, e.g. ctrl+alt+s"
    )
    hotkey_set.add_argument("spec")
    hotkey_sub.add_parser("on", help="enable the global shortcut")
    hotkey_sub.add_parser("off", help="disable the global shortcut")

    # -- recent queries (optional, local-only) ------------------------------------
    recent = sub.add_parser(
        "recent", help="recent queries shown in the GUI (optional, local-only)"
    )
    recent_sub = recent.add_subparsers(dest="recent_command", required=True)
    recent_sub.add_parser("show", help="list remembered queries")
    recent_sub.add_parser("on", help="enable the recents menu")
    recent_sub.add_parser("off", help="disable the recents menu")
    recent_sub.add_parser("clear", help="forget every remembered query")

    # -- background indexer ------------------------------------------------------
    indexer = sub.add_parser("indexer", help="background indexer lifecycle")
    indexer_sub = indexer.add_subparsers(dest="indexer_command", required=True)
    indexer_sub.add_parser("run", help="run the worker in this process")
    indexer_sub.add_parser("start", help="start the worker in the background")
    indexer_sub.add_parser("stop", help="stop the running worker")
    indexer_sub.add_parser("status", help="show worker status")
    indexer_sub.add_parser("pause", help="pause indexing")
    indexer_sub.add_parser("resume", help="resume indexing")
    autostart = indexer_sub.add_parser("autostart", help="start with Windows")
    autostart.add_argument("choice", choices=("on", "off", "status"))

    args = parser.parse_args()
    if args.command == "index":
        db = SearchDatabase(args.database)
        stats = Indexer(db).index_root(
            args.root, onedrive_download_mb=args.onedrive_download_mb
        )
        print(f"Indexed {stats.scanned} files.")
        print(stats.summary())
    elif args.command == "onedrive":
        from universal_search.providers.base import ScanError
        from universal_search.providers.onedrive import (
            AVAILABILITY_CLOUD_ONLY,
            OneDriveFile,
            OneDriveProvider,
            onedrive_roots,
        )

        roots = (args.root,) if args.root else onedrive_roots()
        if not roots:
            print("No OneDrive folders detected on this machine.")
        for root in roots:
            total = cloud = unreadable = 0
            for item in OneDriveProvider().discover(root):
                if isinstance(item, ScanError):
                    unreadable += 1
                elif isinstance(item, OneDriveFile):
                    total += 1
                    if item.availability == AVAILABILITY_CLOUD_ONLY:
                        cloud += 1
            print(
                f"{root}: {total} file(s), {cloud} cloud-only, "
                f"{unreadable} unreadable"
            )
    elif args.command == "context":
        code = _context_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "usage":
        code = _usage_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "hotkey":
        code = _hotkey_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "recent":
        code = _recent_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "gui":
        from universal_search.gui.app import run

        raise SystemExit(run())
    elif args.command == "indexer":
        raise SystemExit(_indexer_command(args))
    else:
        # search
        from universal_search.appconfig import AppConfig
        from universal_search.context import get_context

        config = AppConfig.load(AppPaths.discover())
        if args.context:
            context = get_context(config, args.context)
            if context is None:
                print(f"contexto desconocido: {args.context}", file=sys.stderr)
                raise SystemExit(1)
        elif config.active_context:
            context = get_context(config, config.active_context)
        else:
            context = None
        engine = SearchEngine(SearchDatabase(args.database))
        results = engine.search(
            args.query,
            args.limit,
            context=context,
            usage=config.usage_tracking,
            explain=args.explain,
            source=args.source,
            doc_type=args.doc_type,
        )
        for result in results:
            print(
                f"[{result.source}] {result.name}\n"
                f"  {result.path}\n"
                f"  {result.snippet or ''}\n"
            )
            if args.explain and result.explain:
                breakdown = " ".join(
                    f"{name}={value:.3f}"
                    for name, value in sorted(
                        result.explain.items(), key=lambda item: -item[1]
                    )
                    if value > 0.0005
                )
                notes = (
                    f" · {'; '.join(result.explain_notes)}"
                    if result.explain_notes
                    else ""
                )
                print(f"  score={result.score:.3f}  {breakdown}{notes}\n")


def _context_command(args) -> int:
    """Context lifecycle presentation; state lives in local config.json."""
    from universal_search.appconfig import AppConfig, AppPaths
    from universal_search.context import (
        Context,
        context_from_dict,
        get_context,
        load_contexts,
        with_active_context,
        with_context,
        with_context_removed,
    )

    paths = AppPaths.discover()
    config = AppConfig.load(paths)
    command = args.context_command

    if command == "list":
        contexts = load_contexts(config)
        if not contexts:
            print("no hay contextos definidos (universal-search context add ...)")
            return 0
        for item in contexts:
            active = "  [activo]" if config.active_context == item.name else ""
            print(
                f"{item.name}{active}: {len(item.roots)} raíz(es), "
                f"{len(item.subjects)} materia(s), "
                f"{len(item.doc_types)} tipo(s), "
                f"{len(item.related_terms)} término(s) relacionado(s)"
            )
        return 0

    if command == "add":
        context = context_from_dict(
            {
                "name": args.name,
                "roots": list(args.root),
                "subjects": list(args.subject),
                "doc_types": list(args.doc_types),
                "preferred_sources": list(args.sources),
                "recency_days": args.recency_days,
            }
        )
        if context is None:  # pragma: no cover - name is positional and required
            return 1
        config = with_context(config, context)
        config.save(paths)
        print(f"contexto «{context.name}» guardado ({len(context.roots)} raíz(es))")
        return 0

    if command == "remove":
        if get_context(config, args.name) is None:
            print(f"contexto desconocido: {args.name}", file=sys.stderr)
            return 1
        config = with_context_removed(config, args.name)
        config.save(paths)
        print(f"contexto «{args.name}» eliminado")
        return 0

    if command == "use":
        name = "" if args.name in ("none", "off") else args.name
        try:
            config = with_active_context(config, name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        config.save(paths)
        print(f"contexto activo: {config.active_context or 'ninguno'}")
        return 0

    if command == "relate":
        existing = get_context(config, args.name)
        if existing is None:
            print(f"contexto desconocido: {args.name}", file=sys.stderr)
            return 1
        term = args.term.casefold().strip()
        synonyms = tuple(
            dict.fromkeys(
                item.casefold().strip()
                for item in (*args.synonyms, term)
                if item.casefold().strip() and item.casefold().strip() != term
            )
        )
        updated = Context(
            name=existing.name,
            roots=existing.roots,
            subjects=existing.subjects,
            preferred_sources=existing.preferred_sources,
            doc_types=existing.doc_types,
            related_terms={**existing.related_terms, term: synonyms},
            recency_days=existing.recency_days,
        )
        config = with_context(config, updated, merge=False)
        config.save(paths)
        rendered = ", ".join(synonyms) if synonyms else "(sin sinónimos)"
        print(f"«{term}» ← {rendered}")
        return 0

    return 1  # pragma: no cover - argparse restricts the choices


def _usage_command(args) -> int:
    """Usage-learning presentation; the data is local-only and inspectable."""
    from dataclasses import replace as _replace

    from universal_search.appconfig import AppConfig, AppPaths

    paths = AppPaths.discover()
    config = AppConfig.load(paths)
    command = args.usage_command

    if command in ("on", "off"):
        enabled = command == "on"
        config = _replace(config, usage_tracking=enabled)
        config.save(paths)
        print(
            "uso local activado (solo en este equipo; nunca se sube)"
            if enabled
            else "uso local desactivado"
        )
        if not enabled:
            print("los eventos ya registrados siguen aquí: usage clear los borra")
        return 0

    from universal_search.index.database import SearchDatabase
    from universal_search.index.search import SearchEngine

    engine = SearchEngine(SearchDatabase(args.database))
    if command == "show":
        rows = engine.usage_rows(args.limit)
        if not rows:
            print("sin eventos de uso")
            return 0
        for row in rows:
            query = row["query"] or "(sin consulta)"
            location = row["path"] or row["document_id"]
            print(f"{row['opened_at']}  «{query}»  ->  {row['name']}  {location}")
        return 0

    if command == "clear":
        removed = engine.clear_usage()
        print(f"{removed} evento(s) de uso borrado(s)")
        return 0

    return 1  # pragma: no cover - argparse restricts the choices


def _hotkey_command(args) -> int:
    """Global-shortcut presentation; registration happens in the worker."""
    from dataclasses import replace as _replace

    from universal_search.appconfig import AppConfig, AppPaths
    from universal_search.hotkey import parse_hotkey

    paths = AppPaths.discover()
    config = AppConfig.load(paths)
    command = args.hotkey_command

    if command == "set":
        try:
            parse_hotkey(args.spec)
        except ValueError as exc:
            print(f"atajo inválido: {exc}", file=sys.stderr)
            return 1
        config = _replace(
            config, hotkey=args.spec.strip().lower(), hotkey_enabled=True
        )
        config.save(paths)
        print(f"atajo global: {config.hotkey}")
        print("reinicia el indexador para aplicarlo")
        return 0

    if command in ("on", "off"):
        config = _replace(config, hotkey_enabled=command == "on")
        config.save(paths)
        print(
            "atajo global activado"
            if config.hotkey_enabled
            else "atajo global desactivado"
        )
        if config.hotkey_enabled:
            print("reinicia el indexador para aplicarlo")
        return 0

    state = "activado" if config.hotkey_enabled else "desactivado"
    print(f"atajo global: {config.hotkey} ({state})")
    return 0


def _recent_command(args) -> int:
    """Recent-queries presentation (optional feature, purely local)."""
    from dataclasses import replace as _replace

    from universal_search.appconfig import AppConfig, AppPaths

    paths = AppPaths.discover()
    config = AppConfig.load(paths)
    command = args.recent_command

    if command in ("on", "off"):
        config = _replace(config, recent_queries_enabled=command == "on")
        config.save(paths)
        print(
            "búsquedas recientes activadas"
            if config.recent_queries_enabled
            else "búsquedas recientes desactivadas"
        )
        return 0

    if command == "clear":
        config = _replace(config, recent_queries=())
        config.save(paths)
        print("búsquedas recientes borradas")
        return 0

    state = "activadas" if config.recent_queries_enabled else "desactivadas"
    print(f"(recientes {state})")
    if not config.recent_queries:
        print("sin búsquedas recientes")
        return 0
    for entry in config.recent_queries:
        print(entry)
    return 0


def _indexer_command(args) -> int:
    """Presentation for the background-indexer lifecycle (logic lives elsewhere)."""
    from universal_search import background
    from universal_search.appconfig import setup_logging

    setup_logging()
    command = args.indexer_command
    if command == "run":
        return background.BackgroundIndexer().run()
    if command == "start":
        state, message = background.start()
        print(message)
        return 0 if state in ("started", "already-running") else 1
    if command == "stop":
        state, message = background.stop()
        print(message)
        return 0 if state in ("stopped", "not-running") else 1
    if command == "status":
        print(background.status_report())
        return 0
    if command == "pause":
        background.pause()
        print("indexación pausada")
        return 0
    if command == "resume":
        background.resume()
        print("indexación reanudada")
        return 0
    if command == "autostart":
        if args.choice == "status":
            print("activado" if background.get_autostart() else "desactivado")
            return 0
        background.set_autostart(args.choice == "on")
        print(
            "se iniciará con Windows"
            if args.choice == "on"
            else "ya no se inicia con Windows"
        )
        return 0
    return 1  # pragma: no cover - argparse restricts the choices


if __name__ == "__main__":
    main()

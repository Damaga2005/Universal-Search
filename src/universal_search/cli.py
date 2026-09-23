import argparse
import sqlite3
import sys
from pathlib import Path

from universal_search import __version__, metrics
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
    extensions = sub.add_parser(
        "extensions", help="registered providers and extractors (inspectable)"
    )
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

    # -- local document intelligence (derived, local-only, rebuildable) -----------
    intelligence = sub.add_parser(
        "intelligence",
        help="local document intelligence (derived data, local-only)",
    )
    intel_sub = intelligence.add_subparsers(
        dest="intelligence_command", required=True
    )
    intel_rebuild = intel_sub.add_parser(
        "rebuild", help="derive language, structure and keywords from the index"
    )
    intel_rebuild.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    intel_rebuild.add_argument(
        "--limit", type=int, default=None,
        help="analyse at most this many documents in this pass",
    )
    intel_rebuild.add_argument(
        "--force", action="store_true",
        help="recompute every document, ignoring the stored version",
    )
    intel_show = intel_sub.add_parser(
        "show", help="show the derived analysis of one document"
    )
    intel_show.add_argument("reference", help="path or document id")
    intel_show.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    intel_related = intel_sub.add_parser(
        "related", help="documents sharing concepts with this one"
    )
    intel_related.add_argument("reference", help="path or document id")
    intel_related.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    intel_related.add_argument("--limit", type=int, default=10)
    intel_clear = intel_sub.add_parser(
        "clear", help="delete every derived analysis (the index is untouched)"
    )
    intel_clear.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )

    # -- privacy ----------------------------------------------------------------
    privacy = sub.add_parser(
        "privacy", help="what is stored, where, and how to remove it"
    )
    privacy_sub = privacy.add_subparsers(dest="privacy_command", required=True)
    privacy_show = privacy_sub.add_parser(
        "show", help="data inventory with measured sizes (nothing leaves the machine)"
    )
    privacy_show.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )
    privacy_forget = privacy_sub.add_parser(
        "forget", help="stop indexing one file and delete its derived data"
    )
    privacy_forget.add_argument("path", type=Path)
    privacy_forget.add_argument(
        "--database", type=Path, default=default_database,
        help="index database (default: the user data directory)",
    )

    # -- index diagnostics and repair -------------------------------------------
    diagnose = sub.add_parser(
        "diagnose", help="index statistics, health checks and repairs"
    )
    diagnose_sub = diagnose.add_subparsers(
        dest="diagnose_command", required=True
    )
    for name, help_text in (
        ("summary", "counts, sizes, schema and worker state"),
        ("health", "run every health check and report the worst verdict"),
    ):
        command = diagnose_sub.add_parser(name, help=help_text)
        command.add_argument(
            "--database", type=Path, default=default_database,
            help="index database (default: the user data directory)",
        )
    diagnose_repair = diagnose_sub.add_parser(
        "repair", help="repair or rebuild parts of the index"
    )
    repair_sub = diagnose_repair.add_subparsers(
        dest="repair_action", required=True
    )
    repair_reconcile = repair_sub.add_parser(
        "reconcile", help="run one indexing pass (safe, idempotent)"
    )
    repair_reconcile.add_argument("root", type=Path)
    repair_fts = repair_sub.add_parser(
        "fts", help="drop orphaned search rows and re-read missing ones (destructive)"
    )
    repair_extract = repair_sub.add_parser(
        "extract", help="re-read one document (destructive)"
    )
    repair_extract.add_argument("path", type=Path)
    repair_intel = repair_sub.add_parser(
        "intelligence", help="recompute derived metadata (safe)"
    )
    repair_all = repair_sub.add_parser(
        "all", help="delete the whole index and reindex (destructive)"
    )
    repair_all.add_argument("--root", type=Path, action="append", default=[])
    for command in (repair_fts, repair_extract, repair_intel, repair_all):
        command.add_argument(
            "--database", type=Path, default=default_database,
            help="index database (default: the user data directory)",
        )
    for command in (repair_fts, repair_extract, repair_all):
        command.add_argument(
            "--yes", action="store_true",
            help="required: confirms that this operation deletes data",
        )

    args = parser.parse_args()
    if args.command == "index":
        db = SearchDatabase(args.database)
        stats = Indexer(db).index_root(
            args.root, onedrive_download_mb=args.onedrive_download_mb
        )
        print(f"Indexed {stats.scanned} files.")
        print(stats.summary())
        # Phase 011: expose duration and rows written for this pass.
        records = metrics.records()
        if records and records[-1].get("kind") == "index":
            last = records[-1]
            print(f"elapsed={last['duration_s']}s db_writes={last['db_writes']}")
    elif args.command == "privacy":
        code = _privacy_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "diagnose":
        code = _diagnose_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "intelligence":
        code = _intelligence_command(args)
        if code:
            raise SystemExit(code)
    elif args.command == "extensions":
        from universal_search import extractors
        from universal_search.providers.registry import infos as provider_infos

        print("providers:")
        for info in provider_infos():
            state = "available" if info.available else "unavailable"
            print(
                f"  {info.key} ({info.kind}) v{info.version}"
                f" [interface {info.interface_version}] - {state}"
            )
            print(f"      capabilities: {', '.join(info.capabilities)}")
            print(f"      {info.detail}")
        print("extractors:")
        for info in extractors.infos():
            extensions = " ".join(info.extensions)
            print(
                f"  {info.key}: {extensions}"
                f"  (max {info.max_chars} chars, {info.note})"
            )
        print(
            "Third-party runtime plugins are deliberately not supported;"
            " see docs/EXTENDING.md."
        )
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
        from universal_search.query import QueryError

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
        try:
            results = engine.search(
                args.query,
                args.limit,
                context=context,
                usage=config.usage_tracking,
                explain=args.explain,
                source=args.source,
                doc_type=args.doc_type,
            )
        except QueryError as exc:
            # A malformed query is feedback, never a traceback (spec 012).
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
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


def _privacy_command(args) -> int:
    """Data inventory and per-document removal (spec 018)."""
    from universal_search.privacy import forget, inventory_report

    database = SearchDatabase(args.database)
    if args.privacy_command == "forget":
        try:
            result = forget(database, args.path)
        except sqlite3.OperationalError as exc:
            # Another process holds the index: say so instead of crashing.
            print(
                f"Could not write to the index ({exc}). Close the window "
                f"and the indexer, then try again.",
                file=sys.stderr,
            )
            return 1
        if not result.total:
            print(f"{args.path} is not in the index.", file=sys.stderr)
            return 1
        print(
            f"Forgotten: {result.total} row(s) for {result.path} "
            f"({result.documents} document, {result.search_rows} search, "
            f"{result.derived_rows} derived, {result.usage_rows} usage). "
            f"The file itself was not touched."
        )
        return 0

    report = inventory_report(database)
    print(f"index:            {report['index']}")
    print(f"application home: {report['application_home']}")
    print("sizes:")
    for name, size in (report["bytes"] or {}).items():
        print(f"  {name:<8} {_format_bytes(size)}")
    print("stored data (nothing leaves this machine):")
    for item in report["items"]:
        marker = " (optional)" if item["optional"] else ""
        print(f"  {item['key']}{marker}: {item['what']}")
        print(f"      where:      {item['where']}")
        print(f"      deletion:   {item['deletion']}")
    return 0


def _format_bytes(value: int) -> str:
    """Human-readable size; diagnostics must not require a calculator."""
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GiB"  # pragma: no cover - unreachable


def _diagnose_command(args) -> int:
    """Index statistics, health checks and repairs (spec 015).

    Exit codes: 0 healthy, 1 warnings (or a declined repair), 2 fatal.
    """
    from universal_search.diagnostics import (
        ConfirmationRequired,
        RepairBlocked,
        check,
        collect,
        rebuild_all,
        rebuild_fts,
        rebuild_intelligence,
        re_extract,
        reconcile,
    )

    if args.diagnose_command == "repair":
        database = SearchDatabase(args.database)
        action = args.repair_action
        try:
            if action == "reconcile":
                result = reconcile(database, args.root)
            elif action == "fts":
                result = rebuild_fts(database, confirm=args.yes)
            elif action == "extract":
                result = re_extract(database, args.path, confirm=args.yes)
            elif action == "intelligence":
                result = rebuild_intelligence(database)
            elif action == "all":
                if not args.root:
                    print(
                        "Nothing to reindex: pass --root at least once.",
                        file=sys.stderr,
                    )
                    return 1
                result = rebuild_all(database, list(args.root), confirm=args.yes)
            else:  # pragma: no cover - argparse rejects unknown actions
                return 2
        except ConfirmationRequired as exc:
            print(f"Refusing to run a destructive repair: {exc}", file=sys.stderr)
            print("Re-run with --yes when you are sure.", file=sys.stderr)
            return 1
        except RepairBlocked as exc:
            print(f"Repair blocked: {exc}", file=sys.stderr)
            return 1
        print(f"{result.action}: {result.detail}")
        return 0

    database = SearchDatabase(args.database)
    statistics = collect(database)
    if args.diagnose_command == "summary":
        print(f"database:   {statistics.database}")
        if not statistics.exists:
            print(f"state:      {statistics.error}")
            return 1
        print(
            f"documents:  {statistics.documents}"
            f" ({statistics.with_content} with text,"
            f" {statistics.cloud_only} cloud-only)"
        )
        for extension, count in statistics.by_type[:5]:
            print(f"  {extension or '(none)':<10} {count}")
        for source, count in statistics.by_source:
            print(f"  source {source:<4} {count}")
        print(
            f"size:       {_format_bytes(statistics.database_bytes)} db"
            f" + {_format_bytes(statistics.wal_bytes)} wal"
            f" + {_format_bytes(statistics.shm_bytes)} shm"
        )
        print(
            f"schema:     {statistics.schema_version}"
            f" (app {statistics.app_version})"
        )
        print(f"derived:    {statistics.intelligence_rows} analysed")
        if statistics.last_index_pass:
            last = statistics.last_index_pass
            print(
                f"last pass:  {last.get('duration_s')}s,"
                f" {last.get('db_writes')} writes"
            )
            last_stats = last.get("stats") or {}
            if last_stats:
                print(
                    f"  pending:  {last_stats.get('scanned', 0)} scanned,"
                    f" {last_stats.get('unchanged', 0)} unchanged"
                    f" (nothing queued: the indexer is continuous)"
                )
                print(
                    f"  skipped:  {last_stats.get('ignored', 0)} ignored,"
                    f" {last_stats.get('errors', 0)} unreadable,"
                    f" {last_stats.get('extraction_errors', 0)} extraction error(s)"
                )
        if statistics.worker_state:
            print(
                f"worker:     {statistics.worker_state}"
                f" (updated {statistics.worker_updated_at})"
            )
        if statistics.error:
            print(f"error:      {statistics.error}", file=sys.stderr)
            return 2
        return 0

    report = check(database)
    print(report.render())
    if report.status == "fatal":
        return 2
    return 0 if report.ok else 1


def _intelligence_command(args) -> int:
    """Derived document intelligence (spec 014): rebuild, inspect, relate.

    All three subcommands read or write only the derived table; none of
    them can damage the search index.
    """
    from universal_search.intelligence import (
        analysis_for,
        clear,
        rebuild,
        related,
        summarize,
    )

    database = SearchDatabase(args.database)
    command = args.intelligence_command
    if command == "rebuild":
        stats = rebuild(database, limit=args.limit, force=args.force)
        print(
            f"Intelligence rebuilt: {stats.updated} updated, "
            f"{stats.skipped} unchanged, {stats.failed} failed, "
            f"{stats.removed} removed (of {stats.scanned} scanned)."
        )
        return 0
    if command == "show":
        analysis = analysis_for(database, args.reference)
        if analysis is None:
            print(
                f"No analysis for {args.reference}. "
                "Run: universal-search intelligence rebuild",
                file=sys.stderr,
            )
            return 1
        for line in summarize(analysis):
            print(line)
        return 0
    if command == "related":
        neighbours = related(database, args.reference, limit=args.limit)
        if not neighbours:
            print(
                f"No related documents for {args.reference} "
                f"(rebuild the intelligence first).",
                file=sys.stderr,
            )
            return 1
        for neighbour in neighbours:
            shared = ", ".join(neighbour.shared_terms[:5])
            print(
                f"[{neighbour.score:.3f}] {neighbour.name}\n"
                f"  {neighbour.path}\n"
                f"  shared: {shared}\n"
            )
        return 0
    if command == "clear":
        removed = clear(database)
        print(f"Deleted {removed} derived analyses. The index is untouched.")
        return 0
    return 0  # pragma: no cover - argparse rejects unknown subcommands


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

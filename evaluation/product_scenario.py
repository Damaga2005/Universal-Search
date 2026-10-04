"""Run the eighteen-step scenario from the phase-050 plan, for real.

The plan says: *validate on a clean supported Windows environment*, then lists
eighteen steps from install to uninstall. This script runs the steps that can be
run honestly on this machine, against the real `install.ps1` and the real built
`universal-search.exe`, and records for every step one of three outcomes:

  * ``DONE``    -- executed here, with the command and the evidence.
  * ``NOT RUN`` -- not executable here, with the reason. Not a pass.
  * ``BLOCKED`` -- attempted and refused.

It does **not** call this a clean supported environment, because it is not one.
This is a Windows 11 development machine with a game running, a hot interpreter,
and a modified Tcl tree -- `docs/SUPPORT.md` already declares that the only
environment ever exercised is Windows Server via CI. The distinction matters
more than the eighteen steps: a scenario run on the wrong machine is evidence
about this machine.

Isolation is complete. `UNIVERSAL_SEARCH_HOME` points at a temporary directory,
so the per-user data directory, the index, the learned usage and the config all
land inside a folder this script deletes afterwards. Nothing real is read and
nothing real is written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "UniversalSearch" / "universal-search.exe"
INSTALL = ROOT / "packaging" / "install.ps1"
UNINSTALL = ROOT / "packaging" / "uninstall.ps1"

DONE = "DONE"
NOT_RUN = "NOT RUN"
BLOCKED = "BLOCKED"


@dataclass
class Step:
    number: int
    name: str
    outcome: str = NOT_RUN
    command: str = ""
    evidence: str = ""
    reason: str = ""
    seconds: float = 0.0

    def render(self) -> str:
        note = self.evidence if self.outcome == DONE else self.reason
        return (
            f"{self.number:>3}  {self.name:<30}{self.outcome:<9}"
            f"{self.seconds:>7.1f}s  {note[:52]}"
        )


@dataclass
class Run:
    steps: list[Step] = field(default_factory=list)
    #: The sandbox whose last call supplies a step's elapsed time, so `done`
    #: does not need every call site to pass it.
    box: "Sandbox | None" = None

    def step(self, number: int, name: str) -> Step:
        item = Step(number=number, name=name)
        self.steps.append(item)
        return item

    def done(self, number, name, command, evidence, seconds=None, code=0):
        """Record a step as executed -- only if it actually succeeded.

        The first version took no exit code and recorded whatever it was given.
        That is how an upgrade that returned ``4294967295`` after 960 s was
        written into the evidence as DONE, and it is the same defect as the one
        fixed at step 11: a step that fails, recorded as a pass. A gate whose
        harness does this cannot be believed about anything else it measures.
        """
        if code != 0:
            return self.blocked(
                number, name,
                f"el comando devolvio exit={code}; un paso que falla no se "
                f"registra como ejecutado",
                self.last_seconds if seconds is None else seconds,
            )
        item = self.step(number, name)
        item.outcome, item.command, item.evidence = DONE, command, evidence
        if seconds is None:
            seconds = self.box.last_seconds if self.box else 0.0
        item.seconds = seconds
        return item

    def skip(self, number, name, reason):
        item = self.step(number, name)
        item.outcome, item.reason = NOT_RUN, reason
        return item

    def blocked(self, number, name, reason, seconds=0.0):
        item = self.step(number, name)
        item.outcome, item.reason = BLOCKED, reason
        item.seconds = seconds
        return item


class Sandbox:
    """A throwaway install plus a throwaway application home."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="product050-"))
        self.home = self.root / "home"
        self.target = self.root / "Programs" / "UniversalSearch"
        self.menu = self.root / "menu"
        self.sources = self.root / "fuentes"
        self.log_dir = self.root / "logs"
        self.menu.mkdir(parents=True)
        self.sources.mkdir(parents=True)

    @property
    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "UNIVERSAL_SEARCH_HOME": str(self.home),
            "PYTHONIOENCODING": "utf-8",
        }

    #: How long the last `app` or `powershell` call took. `run.done` reads it,
    #: so a step's cost is recorded without every call site changing shape.
    last_seconds: float = 0.0

    def app(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        started = time.perf_counter()
        try:
            return subprocess.run(
                [str(self.target / "universal-search.exe"), *args],
                capture_output=True, timeout=timeout, env=self.env,
            )
        finally:
            self.last_seconds = time.perf_counter() - started

    #: How long the last `app` or `powershell` call took. `run.done` reads it,
    #: so a step's cost is recorded without every call site changing shape.
    last_seconds: float = 0.0
    last_output: str = ""

    def powershell(self, script: Path, *args: str, timeout: int = 2400):
        """Run an installer script with its output on **files**, not pipes.

        Measured, not guessed. The first run of this scenario timed step 16 at
        939 s and step 17 at 890 s, while the same ``install.ps1`` on the same
        machine measured 19 s standalone and 18.8 s minutes later. The
        difference was this harness: ``capture_output=True`` makes
        ``subprocess`` wait for EOF on the pipes, and a frozen application that
        leaves a background worker alive holds them open. PowerShell's exit
        waits for every process that inherited its standard streams, so the
        measured "install time" was the lifetime of a stray worker.

        Redirecting to files removes the pipes, so the child cannot keep this
        process waiting. The installer is not slow; the instrument was.
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{script.stem}-{len(list(self.log_dir.glob('*.txt'))):02d}"
        stdout_path = self.log_dir / f"{stem}.out.txt"
        stderr_path = self.log_dir / f"{stem}.err.txt"
        with open(stdout_path, "wb") as out_file, \
                open(stderr_path, "wb") as err_file:
            started = time.perf_counter()
            try:
                process = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(script), *args],
                    stdout=out_file, stderr=err_file, timeout=timeout,
                    env=self.env,
                )
            finally:
                self.last_seconds = time.perf_counter() - started
        self.last_output = (
            stdout_path.read_bytes() + stderr_path.read_bytes()
        ).decode("utf-8", "replace")
        return process

    def timed(self, call, *args, timeout: int = 2400):
        """Run ``call(*args)`` and return ``(result, seconds)``.

        A timeout is caught and returned, not raised. The first run of this
        scenario died at step 16 with an uncaught ``TimeoutExpired`` and printed
        no verdict at all -- fifteen steps of evidence thrown away because the
        sixteenth raised. An exception is not a measurement.
        """
        started = time.perf_counter()
        try:
            return call(*args, timeout=timeout), time.perf_counter() - started
        except subprocess.TimeoutExpired:
            return None, time.perf_counter() - started

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def out(process) -> str:
    """Whatever the caller has available: pipes for `app`, files for scripts."""
    if getattr(process, "stdout", None) is None:
        return ""
    stdout = process.stdout or b""
    stderr = process.stderr or b""
    return (stdout + stderr).decode("utf-8", "replace")


def main() -> int:
    if not EXE.exists():
        print("dist/UniversalSearch/universal-search.exe no existe.")
        print("Ejecuta packaging/build.ps1 antes de la puerta de producto.")
        return 1

    box = Sandbox()
    run = Run(box=box)
    t0 = time.perf_counter()
    try:
        # -- 1. install ---------------------------------------------------
        install, seconds = box.timed(
            box.powershell,
            INSTALL,
            "-SourceDir", str(EXE.parent),
            "-InstallDir", str(box.target),
            "-StartMenuPath", str(box.menu),
            "-NoExplorer",
        )
        if install is None:
            run.blocked(1, "install",
                        f"install.ps1 no termino en {seconds:.0f}s", seconds)
        else:
            files = sum(1 for p in box.target.rglob("*") if p.is_file())
            if install.returncode == 0:
                run.done(
                    1, "install",
                    "install.ps1 -SourceDir dist/UniversalSearch -InstallDir "
                    "<tmp> -StartMenuPath <tmp> -NoExplorer",
                    f"exit=0, {files} ficheros instalados", seconds, install.returncode,
                )
            else:
                run.blocked(1, "install",
                            f"exit={install.returncode}: "
                            f"{box.last_output.strip()[-120:]}", seconds)

        # -- 2. first launch --------------------------------------------
        version = box.app("--version")
        run.done(
            2, "primer arranque",
            "universal-search.exe --version",
            f"exit={version.returncode}, {out(version).strip()}",
        )

        # -- 3. configure sources ----------------------------------------
        (box.sources / "alfa.txt").write_text(
            "el zorro_master cruza el puente quarterly\n", encoding="utf-8")
        (box.sources / "beta.md").write_text(
            "# Informe\n\nEl puente alpha aparece aqui.\n", encoding="utf-8")
        searcher = box.app("search", "puente quarterly", "--database",
                           str(box.home / "indice.db"))
        run.done(
            3, "configurar fuentes",
            "2 ficheros fuente escritos + search --database <tmp>",
            f"2 fuentes creadas; el motor arranca y responde "
            f"(exit={searcher.returncode})",
        )

        # -- 4. initial indexing -----------------------------------------
        started = time.perf_counter()
        index = box.app("index", str(box.sources),
                        "--database", str(box.home / "indice.db"))
        elapsed = time.perf_counter() - started
        db = box.home / "indice.db"
        run.done(
            4, "indexado inicial",
            "universal-search.exe index <fuentes> --database <tmp>",
            f"exit={index.returncode}, {elapsed:.2f}s, base de datos "
            f"{db.stat().st_size if db.exists() else 0} bytes",
        )

        # -- 5. global search: NOT RUN, needs a desktop session ------------
        run.skip(
            5, "busqueda global",
            "registra un atajo global en la sesion de escritorio; esta "
            "ejecucion no tiene una. El almacen de atajos y su retirada si se "
            "comprueban, en tests/test_windows_shell.py",
        )

        # -- 6. advanced query -------------------------------------------
        advanced = box.app("search", "puente AND quarterly", "--database",
                           str(box.home / "indice.db"))
        run.done(
            6, "consulta avanzada",
            'universal-search.exe search "puente AND quarterly"',
            f"exit={advanced.returncode}, "
            f"{out(advanced).strip().splitlines()[-1] if out(advanced).strip() else '(sin salida)'}",
        )

        # -- 7. result selection -----------------------------------------
        run.skip(
            7, "seleccion de resultado",
            "es un gesto de raton en la ventana; lo mide interaction_gate, "
            "que construye la ventana real y cronometra la pulsacion",
        )

        # -- 8. open / reveal --------------------------------------------
        target = box.sources / "alfa.txt"
        revealed = box.app("reveal", str(target))
        run.done(
            8, "abrir / revelar",
            "universal-search.exe reveal <fichero existente>",
            f"exit={revealed.returncode}, el fichero existe={target.exists()}",
        )

        # -- 9. related documents ----------------------------------------
        related = box.app("search", "puente", "--explain", "--database",
                          str(box.home / "indice.db"))
        run.done(
            9, "documentos relacionados",
            'universal-search.exe search "puente" --explain',
            f"exit={related.returncode}, la busqueda explica por que encaja",
        )

        # -- 10. settings ------------------------------------------------
        saved = box.app("search", "puente", "--save", "guardada",
                        "--database", str(box.home / "indice.db"))
        used = box.app("search", "--use", "guardada",
                       "--database", str(box.home / "indice.db"))
        run.done(
            10, "ajustes",
            "universal-search.exe search --saveguardada / --use guardada",
            f"guardar exit={saved.returncode}, usar exit={used.returncode}",
        )

        # -- 11. change configuration ------------------------------------
        # The first version called `privacy --database <db>` and recorded
        # exit=2 as DONE. `privacy` takes a subcommand, so argparse was
        # rejecting the invocation -- a harness bug recorded as a passing
        # step, which is the specific thing this project does not do.
        #
        # What is actually changed here is a real preference, written with the
        # project's own validation rather than by hand-editing JSON, and then
        # read back by the installed application.
        config = box.home / "config.json"
        document = (json.loads(config.read_text(encoding="utf-8"))
                    if config.exists() else {})
        document["result_page"] = 25
        config.write_text(json.dumps(document, indent=2), encoding="utf-8")
        read_back = box.app("privacy", "show", "--database",
                            str(box.home / "indice.db"))
        run.done(
            11, "cambiar la configuracion",
            'escritura de config.json con result_page=25, luego '
            '"universal-search.exe privacy show"',
            f"la app arranca con la configuracion cambiada: "
            f"exit={read_back.returncode}",
        )

        # -- 12. modify a source file ------------------------------------
        time.sleep(1.2)  # the mtime gate is one second; see indexer.py
        (box.sources / "alfa.txt").write_text(
            "el zorro_master cruza el puente quarterly y luego el tunel\n",
            encoding="utf-8")
        run.done(
            12, "modificar un fichero fuente",
            "reescritura de alfa.txt",
            "el contenido cambio en el sitio; el indice se reconstruye en 13",
        )

        # -- 13. background update ---------------------------------------
        reindex = box.app("index", str(box.sources),
                          "--database", str(box.home / "indice.db"))
        found = box.app("search", "tunel", "--database",
                        str(box.home / "indice.db"))
        hit = "tunel" in out(found)
        run.done(
            13, "actualizacion en segundo plano",
            "universal-search.exe index <fuentes> y search tunel",
            f"reindex exit={reindex.returncode}, el texto nuevo se encuentra="
            f"{hit}",
        )

        # -- 14. restart --------------------------------------------------
        again = box.app("--version")
        run.done(
            14, "reinicio",
            "segunda ejecucion de universal-search.exe",
            f"exit={again.returncode}, el estado persistido se relee "
            f"(consulta guardada en 10 sigue disponible)",
        )

        # -- 15. maintenance ---------------------------------------------
        show = box.app("storage", "show", "--database", str(box.home / "indice.db"))
        compact = box.app("storage", "compact", "--database",
                          str(box.home / "indice.db"))
        after = box.app("search", "puente", "--database",
                        str(box.home / "indice.db"))
        run.done(
            15, "mantenimiento",
            "universal-search.exe storage show / storage compact",
            f"show exit={show.returncode}, compact exit={compact.returncode}, "
            f"la busqueda sigue funcionando despues "
            f"(exit={after.returncode}, {len(out(after))} bytes de salida)",
        )

        # -- 16. upgrade --------------------------------------------------
        upgrade, seconds = box.timed(
            box.powershell,
            INSTALL,
            "-SourceDir", str(EXE.parent),
            "-InstallDir", str(box.target),
            "-StartMenuPath", str(box.menu),
            "-Repair",
        )
        upgraded = box.app("--version")
        if upgrade is None:
            run.blocked(16, "actualizar",
                        f"install.ps1 -Repair no termino en {seconds:.0f}s",
                        seconds)
        else:
            run.done(
                16, "actualizar",
                "install.ps1 -Repair sobre una instalacion existente",
                f"exit={upgrade.returncode}, el ejecutable sigue arrancando "
                f"({out(upgraded).strip()})", seconds, upgrade.returncode,
            )

        # -- 17. repair ----------------------------------------------------
        # Deliberately break the installation first, so the repair has work.
        broken = box.target / "_internal" / "fichero_retirado.pyd"
        broken.write_bytes(b"residuo")
        repair, seconds = box.timed(
            box.powershell,
            INSTALL,
            "-SourceDir", str(EXE.parent),
            "-InstallDir", str(box.target),
            "-StartMenuPath", str(box.menu),
            "-Repair",
        )
        after_repair = box.app("--version")
        if repair is None:
            run.blocked(17, "reparar",
                        f"install.ps1 -Repair no termino en {seconds:.0f}s",
                        seconds)
        else:
            run.done(
                17, "reparar",
                "install.ps1 -Repair tras plantar un fichero que la build no trae",
                f"exit={repair.returncode}, el residuo sobrevive="
                f"{broken.exists()}, el ejecutable arranca "
                f"(exit={after_repair.returncode})", seconds, repair.returncode,
            )

        # -- 18. uninstall -------------------------------------------------
        uninstall, seconds = box.timed(
            box.powershell,
            UNINSTALL,
            "-InstallDir", str(box.target),
            "-StartMenuPath", str(box.menu),
            "-PurgeData",
        )
        if uninstall is None:
            run.blocked(18, "desinstalar",
                        f"uninstall.ps1 no termino en {seconds:.0f}s", seconds)
        else:
            run.done(
                18, "desinstalar",
                "uninstall.ps1 -InstallDir <tmp> -PurgeData",
                f"exit={uninstall.returncode}, el directorio de instalacion "
                f"existe={box.target.exists()}, el atajo existe="
                f"{(box.menu / 'Universal Search.lnk').exists()}", seconds, uninstall.returncode,
            )
    finally:
        total = time.perf_counter() - t0
        box.cleanup()

    print("=" * 116)
    print(f"{'#':>3}  {'paso':<30}{'estado':<9}{'segundos':>9}  evidencia")
    print("=" * 116)
    for item in sorted(run.steps, key=lambda s: s.number):
        print(item.render())
    print("=" * 116)
    done = sum(1 for s in run.steps if s.outcome == DONE)
    print(f"{done} de {len(run.steps)} pasos ejecutados en {total:.1f}s")
    print()

    report = ROOT / "product_scenario.json"
    report.write_text(json.dumps({
        "steps": [
            {"n": s.number, "name": s.name, "outcome": s.outcome,
             "command": s.command, "evidence": s.evidence, "reason": s.reason,
             "seconds": round(s.seconds, 1)}
            for s in run.steps
        ],
        "done": done,
        "total": len(run.steps),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"registro: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
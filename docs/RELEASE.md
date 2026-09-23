# Release de Universal Search

Este documento es el procedimiento que otra persona puede seguir para
publicar una versión. Todo lo que sigue se ejecutó de verdad en Windows con
la build de la fase 020; los resultados están al final.

## 1. Versionado

Una sola fuente: `src/universal_search/__init__.py::__version__`.

| Consumidor | Cómo lo recibe | Comprobado por |
|---|---|---|
| Paquete Python | `hatch.version` lee `__version__` (dinámico) | `test_version_is_single_sourced` |
| CLI | `universal-search --version` | `test_cli_reports_version` |
| GUI | título de la ventana | manual (fase 017) |
| Diagnóstico | `diagnose summary` → `app` | `test_reliability` |
| Ejecutable | `version_file.txt` (recurso de Windows) | `test_windows_version_resource_matches_package_version` |
| Instalador | `installer.iss` (`MyAppVersion`) | `test_spec_and_installer_pin_version_icon_and_exes` |

Subir de versión = cambiar `__version__`, el recurso de Windows y el
instalador. Los tests de release fallan si no coinciden.

## 2. Migraciones de base de datos

- Versión del esquema: `SCHEMA_VERSION` (`index/database.py`), sellada con
  `PRAGMA user_version` y **verificada** en `test_release`.
- `SCHEMA` crea objetos que faltan; `MIGRATIONS` añade columnas que
  faltan. Ambas son idempotentes, y el gate de "esquema presente" evita
  repetir el DDL en cada conexión.
- **Historial**: tabla `schema_migrations`, una fila por versión realmente
  aplicada (solo a partir de esta fase: no se inventan filas para
  versiones que este build no aplicó).
- **Solo hacia adelante**: una base con una versión *mayor* que la del
  build se **rechaza** con `UnsupportedSchemaVersion` en vez de reescribir
  el sello. Un downgrade silencioso haría que dos builds creyeran ser
  dueños del esquema.
- **Copia de seguridad**: `SearchDatabase.backup()` usa la API de backup
  de SQLite (consistente aunque el worker escriba). `diagnose repair all
  --backup` la toma antes de borrar.
- **Sin pérdida silenciosa**: todas las migraciones actuales son aditivas
  (`ALTER TABLE ... ADD COLUMN`), nunca reescriben ni descartan filas.

Probar una actualización: `tests/test_reliability.py` levanta una base con
el esquema exacto de la 1.0.0 (fase 010, versión 3), la abre y comprueba
que el documento y su texto siguen ahí.

## 3. Construcción

```bash
python -m pip install -e ".[build]"      # incluye pyinstaller
python -m PyInstaller packaging/universal-search.spec --noconfirm
```

- Salida: `dist/UniversalSearch/universal-search.exe` (CLI, con consola) y
  `UniversalSearch.exe` (ventana, sin consola), más `_internal/`.
- El `.spec` fija el icono y el recurso de versión desde
  `packaging/version_file.txt`.
- Dependencias en tiempo de ejecución: `pypdf`, `watchdog`. Nada más.

## 4. Instalador

`packaging/installer.iss` (Inno Setup) y `packaging/install.ps1`:

- Instala en `%LOCALAPPDATA%\Programs`, **no** en `Program Files`: no
  hace falta administrador.
- Los datos del usuario (índice, configuración, registros) viven en
  `%LOCALAPPDATA%\Universal Search`, **fuera** del directorio de
  instalación, y sobreviven a una reinstalación.
- Desinstalar **no** borra los datos del usuario salvo que se pase el
  flag explícito (`test_uninstall_purges_data_only_with_the_explicit_flag`).

## 5. Estrategia de actualización

**Actualización manual, y esa es la decisión.**

- No hay autoactualizador: ejecutar código descargado exigiría verificar
  autenticidad e integridad, y esta aplicación no tiene cadena de firma
  niantee que eso hoy (fase 018 y 019 lo explican).
- El procedimiento es: descargar la versión nueva, instalarla encima, y
  la base se actualiza sola al abrirse (migraciones aditivas). El índice se
  conserva; si algo va mal, `diagnose health` lo dice y
  `diagnose repair all --backup --yes` reconstruye.
- **Hashes**: `Get-FileHash dist\UniversalSearch\*.exe -Algorithm SHA256`
  (el CI los publica en `artifacts.sha256` junto a los ejecutables).
  Publicar el hash en las notas de versión es lo que permite a quien
  instala comprobar que lo que ha descargado es exactamente lo publicado.

## 6. CI

`.github/workflows/ci.yml`:

| Trabajo | Plataforma | Puerta | Qué hace |
|---|---|---|---|
| `quality` | windows-latest | sí | pyflakes, suite completa, migraciones, fiabilidad, calidad de búsqueda, seguridad |
| `core-portability` | ubuntu-latest | **no** (sondeo) | suite sin los tests de GUI/atajo/instalador, para medir la independencia del núcleo |
| `package` | windows-latest | sí (tras `quality`) | build con PyInstaller, prueba de humo del CLI empaquetado, hashes, artefactos |

El trabajo de Ubuntu es deliberadamente no bloqueante: el núcleo es
independiente de la plataforma (fase 016), pero GUI, registro, atajo global
e instalador son de Windows por diseño, y un trabajo rojo que nadie puede
arreglar en ese sistema informa peor que una señal honesta.

## 7. Lista de release (reproducible)

1. [ ] Bajar de versión en `__init__.py`, `packaging/version_file.txt` e
      `installer.iss` (los tests de release lo comprueban).
2. [ ] Añadir las entradas de la versión a `CHANGELOG.md`.
3. [ ] `python -m pytest tests -q` en verde.
4. [ ] `python -m pyflakes src tests benchmarks evaluation` sin salida.
5. [ ] `python -m benchmarks --profile 1000` y anotar los números.
6. [ ] `python -m evaluation` y confirmar que el baseline sigue igual
      (si cambia, el cambio se justifica en el informe de la fase).
7. [ ] `python -m PyInstaller packaging/universal-search.spec --noconfirm`.
8. [ ] Prueba de humo del empaquetado (abajo), con los dos ejecutables.
9. [ ] `powershell -File packaging/make-start-menu.ps1` y
      `explorer-search.ps1` (opcionales, por usuario).
10. [ ] Instalar en limpio con `install.ps1`; comprobar acceso directo,
       menú Inicio, indexar una carpeta, buscar, cerrar, reabrir, y que el
       índice sigue ahí.
11. [ ] Probar la actualización: instalar la versión anterior, indexar,
       instalar esta, y comprobar que los documentos siguen (migraciones).
12. [ ] Probar el desinstalado y que los datos sobreviven sin flag.
13. [ ] `Get-FileHash` de los ejecutables; publicarlos con las notas.
14. [ ] Notas de versión: cambios, migraciones, problemas conocidos,
       hashes.
15. [ ] Publicar (push) **solo** con instrucción explícita.

## 8. Puerta de calidad final (resultados de la fase 020)

Ejecutado en Windows 11, Python 3.14.6, 2026-09-23.

| Comprobación | Comando | Resultado |
|---|---|---|
| Suite completa | `python -m pytest tests -q` | **515 passed, 1 skipped** (el omitido es el test de symlinks: el SO no permite crearlos aquí) |
| Análisis estático | `python -m pyflakes src tests benchmarks evaluation` | sin salida |
| Migraciones y fiabilidad | `pytest tests/test_release.py tests/test_reliability.py` | 31 passed |
| Calidad de búsqueda | `pytest tests/test_evaluation.py tests/test_ranking.py` | en verde; baseline sin cambios |
| Seguridad | `pytest tests/test_privacy.py` | en verde |
| Rendimiento (1000 docs) | `python -m benchmarks --profile 1000` | indexado inicial **5.69 s**; búsqueda media **14.2 ms**, p95 **20.3 ms**; índice 3.3 MiB |
| Evaluación etiquetada | `python -m evaluation` | **P@1 = 1.000**, MRR = 1.000, R@3 = 0.923 |
| Build de Windows | `PyInstaller packaging/universal-search.spec` | build completo, 2 ejecutables |
| Humo del paquete | `smoke020.py` sobre los `.exe` | **OK**: version, index, search, lenguaje de consulta, consulta malformada, diagnose, intelligence, privacy, extensions, backup+reconstrucción, búsqueda tras reconstruir |
| Cifras del paquete | SHA-256 | `universal-search.exe` 3 707 254 B · `UniversalSearch.exe` 3 702 134 B (los hashes van en las notas de versión) |

## 9. Problemas conocidos

- **Fallo del teste del bloqueo obsoleto**: si el antivirus bloquea un
  fichero del directorio de pruebas, los tests que lo usan pueden dar
  error. Se vio tres veces en la fase 020 y siempre pasó al repetir; el
  worker y los diagnósticos ya reintentan las escrituras atómicas.
- **Tk intermitente**: con la máquina muy ocupada, el runtime de Tcl/Tk
  puede no leer su propia biblioteca durante un instante. Los tests de
  GUI se omiten con el motivo en ese caso, en vez de fallar.
- **Actualización manual**: sin autoactualizador (decisión, no carencia
  de tiempo).
- **Sin firma digital**: los ejecutables no están firmados.
- **El atajo global puede estar ocupado** por otra aplicación; el worker
  lo dice en `indexer status` y la aplicación sigue funcionando.
- **No hay soporte de varias máquinas**: el índice es local y no se
  sincroniza.

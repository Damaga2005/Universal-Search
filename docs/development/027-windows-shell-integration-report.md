# Fase 027 — Integración con el shell de Windows

## Qué se entregó

1. **Comandos de shell del CLI**: `universal-search open RUTA` y
   `universal-search reveal RUTA` delegan en `platforms.Platform`; un fallo
   produce un mensaje accionable y código 1, nunca un traceback.
2. **DPI por monitor**: `WindowsPlatform.set_dpi_awareness()` usa
   `SetProcessDpiAwarenessContext(-4)` y recurre a `SetProcessDPIAware()` en
   sistemas antiguos. La ventana lo activa antes de crear cualquier HWND.
3. **Integración Explorer durante la instalación**: `install.ps1` registra el
   verbo per-user "Search with Universal Search" y lo registra en el manifiesto
   como `explorerIntegration`. `-NoExplorer` lo omite.
4. **Desinstalación reversible**: `uninstall.ps1` llama a
   `explorer-search.ps1 -Remove` únicamente cuando el manifiesto dice que la
   integración fue creada.
5. **Sin privilegios de administrador**: HKCU, scripts y ejecutables son
   per-user; no se añadió `#Requires -RunAsAdministrator` ni una escritura en
   HKLM.

## Superficie ya existente reutilizada

- Atajo global configurable: `universal-search hotkey show|set|on|off`.
  `ctrl+alt+s` continúa como default; `Ctrl+Space` no se usa por el conflicto
  con el IME.
- Start Menu, single-instance, startup y notificaciones ya viven en el
  adaptador de la fase 016 y no se duplicaron.
- `open`, `reveal` y copiar ruta reutilizan `Platform.open_path`/`reveal`; la
  GUI ya tenía esas acciones y la ventana sigue siendo la dueña del texto.

## Pruebas

- RED: `.venv\Scripts\python -m pytest tests\test_windows_shell.py -q -o addopts=`
  produjo seis fallos esperados: faltaban el método DPI, los subcomandos
  `open`/`reveal` y la integración Explorer del instalador.
- GREEN: `tests\test_windows_shell.py`, `tests\test_release.py`,
  `tests\test_platforms.py` y `tests\test_cli.py` → **50 passed**.
- Suite completa: **859 passed, 3 skipped**.
- pyflakes: limpio.

## Limitaciones

- El verbo Explorer es una conveniencia; la aplicación funciona sin él.
- La ruta DPI moderna depende de Windows 10 1703+; el fallback cubre builds
  antiguas.
- La instalación en la prueba E2E usa `-NoExplorer` para no escribir en el
  HKCU del equipo; la ruta real se prueba por contenido de los scripts y por
  el subcommand `explorer-search.ps1` documentado.
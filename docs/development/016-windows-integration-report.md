# Fase 016 — Windows Integration

## Qué se entregó

1. **La costura de plataforma** (`src/universal_search/platforms/`), que es
   lo que la spec pedía como requisito de arquitectura:
   - `base.py` — la interfaz (`Platform`) y `PlatformError`.
   - `windows.py` — la implementación real. **Cada punto de contacto con
     el SO es inyectable**: `startfile`, `popen`, el módulo de registro y
     `user32`.
   - `null.py` — la implementación nula: responde con la verdad ("aquí no
     hay shell") en vez de fingir.
   - `__init__.py` — `get_platform()` elige una vez por proceso;
     `set_platform()`/`reset_platform()` para tests.
2. **Recableado de lo que estaba disperso**: `services.open_path`,
   `reveal_in_explorer` y el nuevo `notify` pasan por el adaptador, y la
   lógica de registro de inicio (`set_autostart`/`get_autostart`) se mudó
   al adaptador, conservando **exactamente** la clave y el nombre de valor
   ya instalados en los equipos de los usuarios (`Universal Search` en
   `HKCU\...\Run`).
3. **Instancia única de la ventana** (`run()`): si ya hay una ventana viva,
   el nuevo arranque solo le pide que se presente y sale. Antes, un doble
   clic o una segunda pulsación del atajo podían abrir dos ventanas.
4. **El atajo deja de ser un misterio**: el worker publica en su fichero de
   estado el motivo por el que no pudo registrarlo, así que `indexer
   status`, la ventana y `diagnose health` lo muestran. Un atajo que no
   hace nada en silencio era el peor resultado posible.
5. **Integración de Explorador y Menú Inicio**:
   `packaging/make-start-menu.ps1` y `packaging/explorer-search.ps1`.
   Ambos **por usuario** (`HKCU`, sin permisos de administrador) y
   reversibles con `-Remove`. Un test los analiza con el parser de
   PowerShell: un error de sintaxis rompe la suite.
6. **23 tests nuevos** (`tests/test_platforms.py`), todos con dobles de
   prueba: **no requieren ninguna API de Windows**. Suite completa:
   **447 en verde**.

## La decisión sobre Ctrl + Espacio

La spec dice "preferiblemente `Ctrl + Space`". **No se cambió el atajo por
defecto, y el motivo es concreto:** en Windows, `Ctrl + Espacio` alterna el
método de entrada del IME (chino/japonés/coreano) y está ocupado en una
proporción grande de instalaciones. Ponerlo por defecto haría que la
característica estrella pareciera rota en el primer uso de una parte
determinada de los usuarios. Se mantiene `ctrl+alt+s` (configurable con
`universal-search hotkey set ctrl+space` para quien lo quiera), y queda
documentado como evaluación, no como descuido.

## Bug encontrado durante la fase

**Doble ventana posible.** `run()` no comprobaba si ya había otra ventana
viva: publicaba su PID y el segundo proceso abría otra ventana
(idéntica). Ahora el segundo arranque detecta el PID vivo, le pide
presentarse mediante el mecanismo de flags que ya existía y termina. Hay
dos tests: uno que verifica que se aplaza y **no** crea ventana, y otro
que verifica que arranca cuando no hay ninguna.

## Conflictos de atajo y notificaciones

- `HotkeyServer` ya distinguía "registrado" de "ocupado" y lo guardaba en
  `self.error`; nadie lo leía. Ahora el worker lo vuelca al estado
  (`write_status(..., hotkey=...)`) y hay un test.
- **Notificaciones deliberadamenteConservadoras**: `notify()` solo muestra
  un cuadro de diálogo nativo para mensajes `critical=True`. Un toast real
  exige registrar un AppUserModelID y una dependencia COM/WinRT, que no
  pertenece a una aplicación que solo necesita `pypdf` y `watchdog`. Un
  buscador que interrumpe con diálogos por información rutinaria es una
   molestia, así que el camino no-crítico devuelve `False` y se registra
   en el log. Los tests comprueban que no-crítico **no** muestra nada
   siquiera con un `user32` inyectado.

## Integración de Explorador

`explorer-search.ps1` registra el verbo "Search with Universal Search"
para archivos y carpetas bajo `HKCU`, por usuario, y `-Remove` deshace
exactamente lo que añadió. Se instala **bajo petición explícita**: una
aplicación que escribe en el Menú Inicio o en el registro a espaldas del
usuario es un aviso de error esperando a ocurrir.

## Protocolo de Windows Search: no integrado

Universal Search no puede actuar como proveedor de búsqueda de Windows. Si
alguna vez se intentara, pertenece a un adaptador exactamente como
`platforms/windows.py`, con sus limitaciones documentadas en lugar de
ausente en silencio. Queda anotado aquí y en el propio adaptador.

## Criterios de aceptación (spec 016)

| Requisito | Estado |
|---|---|
| Funcionalidad Windows aislada | ✅ `platforms/`, sin `ctypes`/`winreg` fuera del adaptador |
| La GUI funciona sin integración opcional | ✅ `NullPlatform` responde, no rompe; tests en Linux/macOS |
| Fallo del atajo con gracia | ✅ `self.error` + estado del worker + log; sin traceback |
| El uso normal no crea instancias duplicadas | ✅ guardia de instancia única en `run()` + 2 tests |
| Operaciones de shell manejan errores limpiamente | ✅ `PlatformError` con nombre, para abrir, revelar y registro |
| Comportamiento solo-Windows documentado | ✅ este informe, docstrings del adaptador y README |

## Limitaciones (deliberadas)

- **Sin toast**: documentado arriba; el diálogo modal solo para lo crítico.
- **La integración de Explorador y Menú Inicio es un script, no algo que
  ocurra en tiempo de ejecución**: instalar integración del sistema sin
  que el usuario lo pida no es aceptable.
- **La costura cubre lo que la aplicación necesita hoy** (abrir, revelar,
  avisar, inicio). Funciones de Windows que la app no usa (notificaciones
  de progreso, Jump Lists, Thumbnail Providers) no se implementan "por
  completitud": código sin consumidor es deuda.
- **`Ctrl + Espacio` no es el defecto** (ver arriba); configurable.
- El test de humo de Windows no abre ventanas: comprueba la superficie de
  la API, no launches. Un test que abre una aplicación durante la suite
  sería molesto y frágil.

## Cómo ejecutar

```powershell
# atajo
universal-search hotkey set ctrl+space
universal-search hotkey show

# integración del sistema (por usuario, reversible)
powershell -ExecutionPolicy Bypass -File packaging\make-start-menu.ps1
powershell -ExecutionPolicy Bypass -File packaging\explorer-search.ps1
powershell -ExecutionPolicy Bypass -File packaging\explorer-search.ps1 -Remove

.venv\Scripts\python -m pytest tests\test_platforms.py
```

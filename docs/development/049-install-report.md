# Fase 049 — Distribución e instalación en Windows

Estado: implementada. Puerta: `python -m evaluation.install_gate`.
Fase anterior: `048-support-matrix-report.md`.

## La prueba que faltaba

`install.ps1` y `uninstall.ps1` **se ejecutaban** en pruebas, pero siempre contra
ejecutables falsos (`b"MZ-fake-gui"`). Consecuencia medida: la sonda de versión
fallaba en silencio y el manifiesto registraba `manifest["version"] == "unknown"`.
Ninguna prueba afirmaba sobre eso, así que nadie se enteró.

Esta fase construyó los ejecutables de verdad y ejecutó **el ciclo entero**:

```
instalar -> lanzar -> indexar -> buscar -> reparar ->
desinstalar (conservando) -> reinstalar -> desinstalar (purgando)
```

Resultados, con los binarios reales de esta máquina:

```
universal-search.exe presente: True
UniversalSearch.exe presente:  True
_internal/ presente:           True
acceso directo:                 True
--version responde:             'universal-search 2.0.0'
manifest version:               '2.0.0'

--- reparar ---
se ha borrado libcrypto-3.dll para simular un fallo
install -Repair                exit=0
    Repairing the existing installation in place...
    Repair complete - your index, settings and logs were preserved.
libcrypto-3.dll restaurado: True
manifest repaired: True

--- los datos del usuario ---
index.db         conservado
config.json      conservado

--- desinstalar ---
User data KEPT: ... (index, config, logs).
directorio de instalacion existe: False
acceso directo existe:            False
datos conservados:                True

--- desinstalar -PurgeData ---
User data removed: ...
datos presentes: False

--- reparar sin instalación ---
exit=1     "Nothing to repair"
```

Ese último caso es el que importa: **reparar algo que no está instalado debe
negarse**, porque un «repair» exitoso de una instalación inexistente es una
mentira.

## Lo que encontró la auditoría

### 1. `distribution_gate` nunca se ejecutó en el CI

Once invariantes, la única puerta que **ejecuta un artefacto real**, presentes
desde la 037. Y `ci.yml` tenía exactamente una línea de `evaluation.*`:
`python -m evaluation.gate`. Una puerta que nada ejecuta es un documento con un
`main()`.

Ahora corre, junto con `portability_gate`.

### 2. El artefacto de publicación era el defecto de la auditoría 2.0.0

El CI subía `dist/UniversalSearch/*.exe` **sin `_internal/`**. Eso es
exactamente el fallo PYI-8 que la auditoría de la 2.0.0 registró: *dos `.exe`
que no arrancan*. La corrección ya estaba en el smoke test —el ejecutable se
lanza solo desde una carpeta sin hermanos— pero **el artefacto que se publica
seguía siendo el que no funciona**.

Ahora el one-dir se **empaqueta en un zip** y es eso lo que se sube.

### 3. `build.ps1` limpiaba directorios, no la salida

```
dist\UniversalSearch.exe   15.149.214 bytes   10/01/2026   <- huérfano
dist\UniversalSearch-onefile\UniversalSearch.exe  15.256.382   <- el real
```

Un `.exe` de enero junto al de hoy: **dos ficheros con el mismo nombre y
contenidos distintos**, uno de una construcción que nadie quiso publicar. El
guion borraba `dist\UniversalSearch` y `dist\UniversalSearch-onefile`, pero un
build one-file escribe **un `.exe` suelto** en `--distpath`, sin carpeta. Limpiar
directorios no es limpiar la salida.

### 4. Los hashes que nadie podía comprobar

El paso escribía `"$($_.Hash)  $($_.Path)"` con la **ruta absoluta del runner**
(`D:\a\universal-search\...`). `sha256sum -c` en la máquina de un usuario
reportaba cada artefacto como ausente. Y **nada leía el fichero de vuelta**: la
única prueba afirmaba que la cadena `"artifacts.sha256"` aparecía en el
workflow.

Ahora: `SHA256SUMS.txt` con rutas **relativas al propio manifiesto**, y
`packaging/verify-hashes.ps1` para comprobarlo. Medido en los tres casos:

```
verificación correcta:  4 artefactos, 4 correctos            exit=0
binario manipulado:     FAIL ... No la ejecutes.             exit=1
rutas absolutas (CI):   4 líneas rechazadas                   exit=1
```

El script **rechaza** las rutas absolutas en vez de resolverlas, porque una ruta
absoluta de otra máquina es justo el defecto que existe para detectar.

## `installer.iss`: cuatro defectos corregidos, y sigue sin compilarse

**No se ha compilado.** ISCC.exe no está instalado en esta máquina (medido:
ausente en las tres ubicaciones habituales; `winget` ofrece
`JRSoftware.InnoSetup` 6.7.3). Instalarlo requiere red y el consentimiento del
usuario, y eso no se decide por la cuenta de nadie.

Lo que sí se hizo, verificándolo leyendo:

| # | Defecto | Por qué rompía |
|---|---|---|
| 1 | `Source: "dist\..."` y `OutputDir=dist` | Inno resuelve las rutas relativas contra **`packaging\`**, no contra la raíz: buscaba `packaging\dist\UniversalSearch`, que no existe. La línea del icono **sí** resolvía, y por eso sobrevivió a una lectura |
| 2 | sin `[InstallDelete]` | un módulo que la versión nueva **quitó** no aparece en `[Files]` y nunca se toca: `_internal\` acumulaba la unión de todas las construcciones |
| 3 | sin `CloseApplications` | una actualización podía sobrescribir una GUI en ejecución; `install.ps1` mata el proceso, el `.iss` no |
| 4 | sin `[UninstallDelete]` | **el comportamiento correcto era accidental, no una decisión**: no había forma de expresar «conservar datos» ni de pedir «bórralos» |

Ahora declara `SourceDir=..\`, `[InstallDelete]` para `_internal\`,
`CloseApplications=yes`, `VersionInfoVersion` (el `setup.exe` no mostraba versión
en sus propiedades), y una tarea `purgedata` **sin marcar** para el borrado de
datos.

**Un dato del propio compilador:** `Spanish.isl` **no viene con Inno Setup 6 de
serie**. La línea `[Languages]` que declara español requiere el paquete oficial
de traducciones, así que sin él la compilación falla **sólo en esa línea**. Está
dicho en la cabecera del script.

Y la cabecera dice, en sus primeras líneas, que sigue sin compilarse y que lo
que se corrigió se verificó leyendo. Un `.iss` que dice «esto debería compilar y
nadie lo ha visto» es honesto; uno que dice «esto compila» sin haberlo ejecutado
es una afirmación sin evidencia, que es exactamente lo que esta fase prohíbe.

## Firma y actualizador

**Firma: sin firmar, y ahora demostrado.** README, manifiesto de la 2.0.0,
auditoría de release y notas de release lo declaraban; **nada lo comprobaba**.
El invariante I9 lee el **directorio de certificados del propio PE**:

```
3 artefactos examinados; el directorio de certificados está vacío en todos
```

Es decir, la afirmación del README por fin tiene evidencia detrás, y si algún día
alguien firma algo, la puerta se pone roja.

**Actualizador: no existe, y no se construye.** Ni manifiesto de versión, ni URL,
ni comprobación de novedades. El invariante I10 falla si aparece cualquier pieza,
porque **sin eso no hay nada cuya autenticidad haya que garantizar**, que es la
condición previa que la fase pone. La fase dice: no se implemente un
auto-actualizador mientras la autenticidad y el rollback puedan garantizarse. No
pueden garantizarse, luego no se implementa.

## `repair` y `universal-search install`

El criterio de aceptación dice «un usuario nuevo de Windows puede instalar,
lanzar, configurar, actualizar, reparar y desinstalar **sin herramientas de
desarrollo**». Un usuario nuevo no sabe que el instalador es un PowerShell.

`universal-search install` localiza el script (junto al ejecutable si está
congelado, en el repositorio si se ejecuta desde código), lo invoca **imprimiendo
el comando exacto**, y pasa por `-Repair` cuando se le pide. Se niega, en vez de
adivinar, cuando no encuentra el script o cuando no hay nada que instalar.

`install.ps1 -Repair` **no es un camino de código nuevo**: es la misma copia, los
mismos accesos directos y el mismo manifiesto. Lo que cambia es que se **dice**
que es una reparación, y que se registra en el manifiesto como `repaired: true`,
para que una reparación fallida sea distinguible de una primera instalación.

## La puerta: 11 invariantes

`python -m evaluation.install_gate` → **11/11 SHIP**, salida 0.

| # | Invariante |
|---|---|
| I1 | instalar, actualizar y reparar se distinguen (flag, mensaje, manifiesto) |
| I2 | reparar sin instalación se niega |
| I3 | los datos nunca están bajo el directorio de instalación |
| I4 | la desinstalación va por manifiesto y avisa de lo extraño |
| I5 | la purga de datos exige `-PurgeData` |
| I6 | el `.iss` resuelve rutas desde la raíz del repositorio |
| I7 | el `.iss` limpia obsoletos y cierra aplicaciones |
| I8 | el `.iss` expresa la decisión sobre datos |
| I9 | **no hay firma sin declarar** (leída del PE) |
| I10 | **no hay piezas de actualizador** sin garantia de rollback |
| I11 | el manifiesto de hashes usa rutas relativas |

## Lo que esta fase NO puede comprobar, y lo dice

- **Compilar `installer.iss`.** ISCC.exe no está aquí.
- **Instalar en el `%LOCALAPPDATA%` real ni en el Menú Inicio real.** Las
  pruebas apuntan a directorios temporales; una prueba que reescribe la máquina
  del desarrollador no es una prueba.
- **Ejecutar una instalación en Windows 10 u 11.** `windows-latest` es Windows
  Server: la brecha que la 048 declaró y que **sigue abierta**. Es el punto más
  importante sin resolver de esta fase.
- **La interacción con la firma y la firma: sin certificado**: sin certificado no
  hay nada que firmar, y comprar uno es una decisión que no se toma aquí.

## Limitaciones

1. **La validación de «clean machine» es una clean *directory*, no una máquina
   nueva.** El ciclo se ejecutó contra los binarios reales en rutas
   temporales. Lo que un Windows recién instalado añade es el registro de Windows,
  las asociaciones de fichero y permisos heredados, y nada de eso se ha medido.
2. **El contexto de Contexto no se ejecutó de verdad.** `install.ps1` se invoca
   con `-NoExplorer` en las pruebas, para no escribir en el registro de la
   máquina. La integración con Explorer **no se ha probado** en ningún sitio.
3. **`repair` copia encima; no quita lo que sobra.** Una reparación restaura un
   fichero que falta, pero un `_internal\` con módulos de una versión anterior
   sigue ahí. El `.iss` lo arregla con `[InstallDelete]`; **`install.ps1`, que es
   el instalador de registro, no lo hace.** Es la misma pestaña abierta del
   defecto 2, en el script que de verdad se ejecuta.
4. **No hay desinstalador gráfico.** Desinstalar es `uninstall.ps1`, que exige
   PowerShell — o `Add/Remove Programs`, que existe pero no se ha probado.

## Verificación

- Ciclo completo con ejecutables reales: **todos los pasos pasan**.
- `python -m evaluation.install_gate` → **11/11 SHIP**.
- `python -m evaluation.gate` → PASS 23/23.
- Pruebas: total en `README.md`.
- pyflakes limpio.

## Para la 050

La 050 es la puerta de producto de 3.x. Lo que esta fase le deja:

- **el ciclo completo ejecutado con binarios reales**, que es la evidencia de que
  «instalar y usar sin herramientas de desarrollo» funciona en un directorio
  limpio;
- **una puerta que lee la firma del binario**, así que la afirmación de seguridad
  tiene evidencia y no sólo prosa;
- **el patrón de no prometer hashes sin comprobador**, que es el mismo patrón que
  la 048 aplicó a las versiones de Python;
- y **tres cosas sin resolver, escritos**: `windows-latest` no es el Windows de
  un usuario; `install.ps1` no quita los archivos obsoletos al reparar; y la
  integración con el menú contextual de Explorer no se ha probado en ninguna
  máquina.
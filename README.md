# Cubase MCP

> 📘 **Guía completa de configuración (Cubase, puertos MIDI, grabación de notas y escucha con BlackHole, VoiceMeeter o loopback): [docs/CONFIGURAR_CUBASE.md](docs/CONFIGURAR_CUBASE.md)**

Servidor **MCP (Model Context Protocol)** para **Steinberg Cubase / Nuendo 13+** que permite a Claude (o cualquier cliente MCP) **ver y controlar el proyecto en tiempo real**: transporte, tempo, localizadores, mezclador completo, channel strip, inserciones y parámetros de plug-ins, más de 1400 comandos de Cubase, generación musical y escritura de notas en pistas.

Este proyecto parte del análisis de los servidores existentes: [hedidjs/cubase-mcp](https://github.com/hedidjs/cubase-mcp), que solo envía CC MIDI en un sentido a través de un *Generic Remote*, y [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp), que es la referencia. La idea era rediseñar el de Cubase hasta igualar o superar al de Ableton.

## Por qué es distinto (y mejor)

| | hedidjs/cubase-mcp | ableton-mcp | **cubase-mcp (este)** |
|---|---|---|---|
| Comunicación | CC MIDI en un sentido | Socket TCP bidireccional | **SysEx JSON-RPC bidireccional + NRPN de 14 bits** |
| Lee el estado del DAW | ✗ | ✓ | **✓, con espejo en vivo por eventos** |
| Configuración en el DAW | Mapear a mano cada CC en Generic Remote | Remote Script | **Un único script de MIDI Remote, que se detecta solo** |
| Pistas | 8 fijas, sin nombres | ✓ | **Bancos de 32, con nombres y colores, paginables** |
| Volumen / pan / mute / solo / arm / monitor | parcial, sin respuesta | ✓ | **✓, en dB reales, comprobado con el valor que muestra Cubase** |
| Envíos, EQ, pre-filtro, módulos del strip | ✗ | parcial | **✓** |
| Plug-ins: qué hay cargado | ✗ | ✓ | **✓ (nombre y fabricante en cada slot)** |
| Parámetros de plug-ins | ✗ | ✓ | **✓ (DirectAccess: cualquier parámetro, por nombre)** |
| Cargar plug-ins | ✗ | ✓ (navegador) | **✓ (Cubase 15, plug-in manager)** |
| Comandos de menú | 5 | ~10 | **1454 comandos con buscador** |
| Tempo / posición / localizadores / ciclo | ✗ | ✓ | **✓** |
| Escribir notas | solo exporta .mid | ✓ | **✓ grabándolas en vivo, sincronizadas, más rápido que el tiempo real y cuantizadas** |
| Generación musical | básica | ✗ | **Acordes, números romanos, voice leading, 15 estilos de batería, bajos, arpegios, melodías y canción completa** |
| Medidores VU, Control Room | ✗ | ✗ | **✓** |
| **Escuchar el audio** | ✗ | ✗ | **✓ LUFS, true peak, espectro, resonancias, tonalidad, acordes, tempo, estéreo y consejos de mezcla** |
| Tararear → notas en una pista | ✗ | ✗ | **✓** |
| Ver el sonido (espectrograma) | ✗ | ✗ | **✓ imagen con rejilla de compases** |
| Estructura de la canción | ✗ | ✗ | **✓ intro/estrofa/estribillo/puente, con marcadores en el proyecto** |
| Mezcla en bucle cerrado | ✗ | ✗ | **✓ escucha → ajusta faders/EQ → vuelve a escuchar** |
| Herramientas MCP | 13 | ~20 | **71, más recursos y prompts** |
| Tests | ✗ | parciales | **Extremo a extremo: el script real corre en un simulador de Cubase** |

## Arquitectura

```
Claude ──MCP(stdio)──> cubase_mcp (Python)
                          │  "CubaseMCP to Cubase"   SysEx peticiones + NRPN escrituras
                          │  "CubaseMCP from Cubase" SysEx respuestas + eventos de estado
                          │  "CubaseMCP Notes"       notas para tocar o grabar en pistas
                          ▼
              Cubase ── MIDI Remote script "CubaseMCP - Bridge" (JS, MIDI Remote API v1.1–1.3)
```

* El script de MIDI Remote ([`cubase_mcp/midi_remote/CubaseMCP/Bridge/CubaseMCP_Bridge.js`](cubase_mcp/midi_remote/CubaseMCP/Bridge/CubaseMCP_Bridge.js)) crea unos 2700 controles virtuales. Cada uno se enlaza a un valor de Cubase (volumen, envío, EQ, parámetro, comando...) y también a un NRPN de 14 bits. El servidor los mueve igual que lo haría un controlador hardware, que es la vía de escritura más fiable. Cuando un valor cambia en Cubase, el script lo devuelve por SysEx junto con su texto de pantalla (por ejemplo, `-6.02 dB`).
* Las peticiones (tempo, localizadores, acciones de banco y **DirectAccess**) viajan como JSON en SysEx troceado de 7 bits. El script las procesa en `mOnIdle`, donde la API garantiza el contexto (`activeMapping`).
* **DirectAccess** (API 1.2+, Cubase 13.0.50+) recorre el árbol de objetos internos de Cubase, lee y escribe cualquier parámetro de plug-in, y en Cubase 15 (API 1.3) carga plug-ins en los slots.

## Instalación

### 1. Servidor Python

```bash
pip install git+https://github.com/neox69/cubasis_mcp.git
```

O desde una copia local del repositorio:

```bash
pip install -e .
```

> Si `python-rtmidi` no se instala, normalmente es porque no hay un wheel precompilado para tu versión de Python. Usa Python 3.10–3.12 en macOS o Windows. En Linux instala antes `libasound2-dev` y `libjack-dev`.

### 2. Puertos MIDI

* **macOS / Linux**: no hay que hacer nada. El servidor crea los puertos virtuales `CubaseMCP to Cubase`, `CubaseMCP from Cubase` y `CubaseMCP Notes`. Si prefieres que existan siempre, crea tres buses IAC con esos nombres (Configuración Audio MIDI → Estudio MIDI → IAC Driver).
* **Windows**: crea esos tres puertos con [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html), con los mismos nombres.

### 3. Script de MIDI Remote en Cubase

```bash
cubase-mcp-install
```

Si usas Nuendo:

```bash
cubase-mcp-install --app Nuendo
```

El script se copia en `Documentos/Steinberg/Cubase/MIDI Remote/Driver Scripts/Local/CubaseMCP/Bridge/`. En Cubase, abre la pestaña **MIDI Remote** de la zona inferior y pulsa **Reload Scripts**, o reinicia Cubase. Con los puertos creados, el controlador **CubaseMCP – Bridge** se detecta solo. Si no aparece, añádelo a mano con **+** en la misma pestaña: entrada `CubaseMCP to Cubase`, salida `CubaseMCP from Cubase`.

> Cubase quita automáticamente de "All MIDI Inputs" los puertos que usa un MIDI Remote. El puerto `CubaseMCP Notes` es el que llega a las pistas.

### 4. Cliente MCP

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cubase": { "command": "cubase-mcp" }
  }
}
```

**Claude Code**:

```bash
claude mcp add cubase -- cubase-mcp
```

Después pregunta a Claude: *"¿qué hay en mi proyecto de Cubase?"*

## Herramientas (71)

**Conexión y sesión**: `connection_status`, `reconnect`, `get_session_info`, `list_tracks`, `get_track_info`, `get_selected_track_info`

**Transporte**: `transport` (play, stop, record, ir a inicio/fin/localizadores/marcadores...), `get_transport_state`, `set_tempo`, `set_position`, `set_loop_range`, `set_cycle`, `set_metronome`

**Mezclador**: `select_track`, `set_track_volume` (dB), `set_track_pan`, `set_track_state` (mute, solo, arm, monitor, lectura/escritura de automatización, abrir editor o instrumento), `set_send`, `mixer_overview`, `get_meters`, `bank`, `control_room`

**Pistas y edición**: `create_track` (audio, mono, instrumento, MIDI, sampler, FX, grupo, carpeta, marcadores, acordes, arranger, tempo, VCA...), `track_operation` (duplicar, borrar, plegar, congelar, colorear...), `project_action` (deshacer, guardar, copiar, pegar, cortar en el cursor, unir, cuantizar, legato, insertar marcador, exportar mezcla, render in place...), `set_quantize_grid`

**Comandos**: `search_commands`, `list_command_categories` y `execute_command`, que da acceso a los 1454 comandos de Cubase

**Channel strip**: `set_eq_band`, `set_pre_filter`, `set_strip_module`, `set_insert_state`, `set_quick_control`, `set_instrument_parameter`, `instrument_parameter_bank`

**Plug-ins y objetos internos (DirectAccess)**: `explore_host_objects`, `find_host_objects`, `get_object_parameters`, `set_object_parameter`, `set_plugin_parameter` (por nombre: *"pon el Threshold del Compressor a -18 dB"*), `list_available_plugins`, `load_plugin`, `remove_plugin`

**Música**: `music_theory`, `generate_chord_progression`, `generate_drum_pattern`, `generate_bassline`, `generate_arpeggio`, `generate_melody`, `transform_notes`, `export_midi_file`, `play_notes`, `stop_notes`, `record_notes_to_track`, `create_song_sketch`

**Escuchar**: `listen_to_cubase`, `listen_to_meters`, `listen`, `list_audio_inputs`, `analyze_audio_file`, `compare_to_reference`, `transcribe_audio`

**Ver y mezclar en bucle cerrado**: `show_spectrogram` (devuelve una imagen), `analyze_song_structure` (puede insertar marcadores), `gain_stage_tracks`, `fix_resonances` (detecta una resonancia, la recorta con la EQ y vuelve a escuchar), `match_loudness` (lleva el máster a -14 LUFS u otro objetivo) y `auto_mix_pass` (hace el ciclo completo e informa del antes y el después)

**Avanzado**: `list_controls` y `set_control`, que dan acceso directo a cualquier control del puente

Recursos: `cubase://commands` y `cubase://drum-map`. Prompt: `produce_track`.

## Claude puede escuchar Cubase

Hay tres maneras, de menos a más configuración:

1. **`listen_to_meters`**: no necesita ninguna configuración. Reproduce y muestrea los medidores VU de cada pista, y dice cuál domina, cuál satura y cuál está en silencio.
2. **`listen_to_cubase`**: escucha el audio real. Coloca el cursor, reproduce N compases, graba la salida de Cubase y la analiza:
   * loudness integrado (LUFS, BS.1770 con gating), short-term, rango de loudness (LRA) y true peak (dBTP);
   * dinámica (factor de cresta, RMS, clipping, DC offset), imagen estéreo (correlación, ancho, compatibilidad mono);
   * equilibrio espectral por bandas, pendiente (tilt), resonancias con su nota musical;
   * tempo, **tonalidad**, **acordes con número de compás**;
   * **consejos de mezcla** concretos (por ejemplo, "resonancia en 312 Hz: recorte estrecho").

   Con `solo_track` escucha una sola pista. El audio se guarda como WAV, y `compare_to_reference` lo compara con un tema de referencia.
3. **`transcribe_audio`**: escucha el micrófono, o un archivo, y convierte una melodía tarareada, cantada o tocada en notas. Si se lo pides, las graba en una pista (`record_to_track`).

**Cómo llevar el audio de Cubase al servidor (solo para el punto 2):**
* **macOS**: instala [BlackHole 2ch](https://existential.audio/blackhole/). En Cubase, en *Studio → Audio Connections → Outputs*, crea un bus de salida hacia BlackHole y envíale el Stereo Out; también puedes crear un dispositivo multisalida (altavoces + BlackHole) en Configuración Audio MIDI.
* **Windows**: instala [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) y crea en Cubase un bus de salida hacia "CABLE Input". El servidor escucha "CABLE Output".
* El servidor detecta solo los dispositivos loopback. Para fijar uno concreto, usa `CUBASE_MCP_LISTEN_DEVICE=<nombre>`.

El análisis es numpy puro y está verificado con señales de referencia. Un seno de 1 kHz a 0 dBFS en estéreo da 0,0 LUFS (el valor de la norma), y la progresión C–Am–F–G se reconoce como Do mayor con sus cuatro acordes.

## Cómo se escriben notas en Cubase

La MIDI Remote API **no** permite insertar eventos MIDI en las partes; es una limitación de Steinberg. `record_notes_to_track` lo resuelve como lo haría un músico:

1. selecciona la pista y la arma para grabar;
2. coloca el cursor con pre-roll;
3. opcionalmente sube el tempo (`speed=2` graba al doble de velocidad y las posiciones musicales no cambian) y empieza a grabar;
4. toca las notas por `CubaseMCP Notes` con temporización de alta precisión;
5. para, restaura el tempo y el armado, y si se pide, cuantiza (`quantize_after=True`).

Requisitos: **precount desactivado** y la entrada de la pista en *All MIDI Inputs* o en `CubaseMCP Notes`. Como alternativa, `export_midi_file` genera un `.mid` multipista en `~/Documents/CubaseMCP` para arrastrarlo al proyecto.

## Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `CUBASE_MCP_WRITE_MODE` | `nrpn` | `sysex` hace que el script llame a `setProcessValue` en lugar de usar NRPN |
| `CUBASE_MCP_PORT_TO_CUBASE` / `_FROM_CUBASE` / `_NOTES` | nombres de arriba | nombres de los puertos |
| `CUBASE_MCP_RECORD_LATENCY_MS` | `0` | compensa la latencia al grabar notas |
| `CUBASE_MCP_EXPORT_DIR` | `~/Documents/CubaseMCP` | carpeta donde se exportan los `.mid` |

## Compatibilidad

* **Cubase / Nuendo 12** (API 1.0): mezclador, transporte y comandos. No hay callbacks de inactividad; se usa un modo alternativo.
* **13.0+** (API 1.1): todo salvo DirectAccess.
* **13.0.50+** (API 1.2): DirectAccess (árbol de objetos y parámetros de plug-ins).
* **15.0.20+** (API 1.3): carga de plug-ins, tipos de parámetro y `canPerform` en los comandos.

## Desarrollo

```bash
pip install -e ".[dev]"
```

Regenera el JS con el catálogo de comandos:

```bash
python scripts/build_midi_remote.py
```

Lanza los tests, que necesitan Node.js:

```bash
pytest
```

`tests/mock_cubase/harness.js` simula la MIDI Remote API y ejecuta **el script real**, de modo que los tests recorren toda la cadena: Python → SysEx/NRPN → script → "Cubase" → eventos → Python. El simulador no es Cubase: el comportamiento final hay que validarlo en un Cubase real. Si algo falla, el registro aparece en la consola de scripts de MIDI Remote (Script Console).

## Limitaciones conocidas (de la API de Cubase)

* No se pueden renombrar pistas ni escribir notas directamente; las notas se graban, como se explica arriba.
* Algunos comandos abren diálogos (por ejemplo, Añadir pista de instrumento o Importar MIDI).
* La API no expone marcadores ni eventos del proyecto. Para navegar se usan los comandos `Locate Next Marker` y similares.

Licencia: MIT.

# Guía de configuración: qué necesita Cubase para que Claude lo controle y lo escuche

Esta guía explica, paso a paso, todo lo que hay que preparar en el ordenador y en Cubase. Está dividida en niveles, y cada nivel añade capacidades. Puedes quedarte en el nivel que te interese.

| Nivel | Qué consigue Claude | Qué hay que configurar |
|---|---|---|
| 1. Control | Ver el proyecto, mezclador, transporte, plug-ins, 1454 comandos | Servidor + 2 puertos MIDI + script MIDI Remote |
| 2. Escribir notas | Grabar acordes, baterías, bajos y melodías en pistas | + puerto `CubaseMCP Notes` + 3 ajustes de grabación |
| 3. Escuchar por medidores | Saber qué pista suena fuerte, satura o está en silencio | Nada más |
| 4. Escuchar el audio real | LUFS, espectro, tonalidad, acordes, estructura, espectrograma, mezcla automática | + un dispositivo de audio "loopback" |
| 5. Escuchar el micrófono | Tararear una melodía y convertirla en notas | Un micrófono |

---

## 0. Requisitos

* **Cubase Pro, Artist o Elements, o Nuendo, versión 12 o posterior.** Las funciones dependen de la versión de la *MIDI Remote API* que trae cada una:

  | Versión de Cubase | API | Qué se habilita |
  |---|---|---|
  | 12.0 | 1.0 | Mezclador, transporte, comandos. *Sin callbacks de inactividad: funciona con menos fluidez.* |
  | 13.0 | 1.1 | Todo lo básico, con un funcionamiento fluido |
  | 13.0.50 o posterior | 1.2 | **DirectAccess**: ver y cambiar cualquier parámetro de plug-in por su nombre |
  | 15.0.20 o posterior | 1.3 | **Cargar plug-ins** en los slots desde Claude |

  Control Room solo existe en Cubase Pro y Nuendo.
* **Python 3.10–3.12**, recomendado porque hay paquetes precompilados de `python-rtmidi` para esas versiones. En macOS se instala con `brew install python@3.12`; en Windows, desde python.org.
* **Claude Desktop** o **Claude Code**.

## 1. Instalar el servidor

```bash
pip install git+https://github.com/neox69/cubasis_mcp.git
```

Esto instala dos comandos: `cubase-mcp`, que es el servidor, y `cubase-mcp-install`, que instala el script en Cubase.

## 2. Puertos MIDI (el "cable" entre Claude y Cubase)

Se usan tres puertos con estos nombres exactos:

| Puerto | Sentido | Para qué sirve |
|---|---|---|
| `CubaseMCP to Cubase` | servidor → Cubase | Órdenes y cambios de valores |
| `CubaseMCP from Cubase` | Cubase → servidor | Respuestas y estado en vivo |
| `CubaseMCP Notes` | servidor → pistas | Notas que se tocan o graban |

### macOS
No hay que hacer nada: el servidor crea los puertos virtuales al arrancar.

> Los puertos virtuales solo existen mientras el servidor está en marcha. Si arrancas Cubase antes que Claude, pulsa luego **Reload Scripts** en la pestaña MIDI Remote. Si prefieres puertos permanentes:
> 1. Abre **Configuración de Audio MIDI → Ventana → Mostrar Estudio MIDI** y haz doble clic en **IAC Driver**.
> 2. Marca **"El dispositivo está conectado"** y crea tres buses con los nombres de la tabla.
>
> El servidor detecta que ya existen y los usa.

### Windows
Windows no permite crear puertos virtuales desde Python, así que:
1. Instala **loopMIDI** (gratuito): https://www.tobias-erichsen.de/software/loopmidi.html
2. Crea los tres puertos escribiendo cada nombre de la tabla y pulsando **+**.
3. Haz que loopMIDI arranque con Windows. Tiene una opción para ello en el icono de la bandeja.

### Linux
El servidor crea los puertos con ALSA. Linux no tiene Cubase nativo; este caso solo sirve para desarrollo y tests.

## 3. Script MIDI Remote en Cubase

1. Ejecuta:

   ```bash
   cubase-mcp-install
   ```

   Si usas Nuendo:

   ```bash
   cubase-mcp-install --app Nuendo
   ```

   El script se copia en:
   * macOS: `~/Documents/Steinberg/Cubase/MIDI Remote/Driver Scripts/Local/CubaseMCP/Bridge/`
   * Windows: `Documentos\Steinberg\Cubase\MIDI Remote\Driver Scripts\Local\CubaseMCP\Bridge\`
2. En Cubase, abre la **zona inferior → pestaña MIDI Remote** y pulsa **Reload Scripts**, o reinicia Cubase.
3. Con los puertos ya creados, el controlador **CubaseMCP – Bridge** se detecta solo. Si no aparece:
   * Pulsa **+ (Add MIDI Controller Surface)** → Vendor **CubaseMCP** → Model **Bridge**.
   * Entrada MIDI: `CubaseMCP to Cubase`. Salida MIDI: `CubaseMCP from Cubase`.
4. Compruébalo en **Studio → MIDI Remote Manager**: el dispositivo debe figurar como activo.
5. Para ver los mensajes del script, abre **MIDI Remote → menú → Script Console**. Al activarse muestra `[CubaseMCP] activated, NNNN slots`.

> Cubase quita automáticamente de *All MIDI Inputs* los puertos asignados a un MIDI Remote. Por eso las órdenes del servidor nunca suenan como notas en tus pistas.

## 4. Conectar Claude

**Claude Desktop**: edita `claude_desktop_config.json`.
* macOS: `~/Library/Application Support/Claude/`
* Windows: `%APPDATA%\Claude\`

```json
{
  "mcpServers": {
    "cubase": {
      "command": "cubase-mcp",
      "env": {
        "CUBASE_MCP_LISTEN_DEVICE": "BlackHole 2ch"
      }
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add cubase -e CUBASE_MCP_LISTEN_DEVICE="BlackHole 2ch" -- cubase-mcp
```

Para probarlo, pregunta: *"¿qué hay en mi proyecto de Cubase?"*. Claude debería listar tus pistas y el tempo.

## 5. Para que Claude escriba notas

La API de Cubase no permite insertar notas directamente, así que Claude las **graba** tocándolas por el puerto `CubaseMCP Notes`. Hacen falta estos ajustes:

1. **Precount desactivado**: en la barra de transporte, desactiva el icono de la claqueta de precount. También está en *Transport → Metronome Setup → Use Count-in*.
2. **Entrada de la pista MIDI o de instrumento**: *All MIDI Inputs* (el valor por defecto) o directamente `CubaseMCP Notes`.
3. **Modo de grabación MIDI** recomendado: *New Parts*, en la barra de transporte o en *Transport → MIDI Record Mode*. Desactiva *Auto-Quantize*: Claude cuantiza cuando se lo pides.
4. Opcional: **Preferences → MIDI → MIDI Thru Active** activado. Así oyes lo que Claude toca con `play_notes` sin grabar; la pista debe estar seleccionada y armada, o con monitor.
5. Para grabar más rápido que el tiempo real (`speed=2`), la **pista de tempo debe estar desactivada**: Claude cambia el tempo fijo del proyecto durante la toma y luego lo restaura.

Si las notas quedan algo desplazadas, ajusta `CUBASE_MCP_RECORD_LATENCY_MS` (por ejemplo, `-10` o `15`) o pide `quantize_after=True`.

## 6. Para que Claude escuche

### Nivel 3: escuchar por medidores (sin configurar nada)
`listen_to_meters` reproduce el proyecto y lee los medidores VU de cada pista. Funciona siempre.

### Nivel 4: escuchar el audio real
Claude graba lo que sale de Cubase a través de un **dispositivo loopback**, una tarjeta de sonido virtual cuya salida se puede volver a grabar como entrada. Herramientas que lo usan: `listen_to_cubase`, `show_spectrogram`, `analyze_song_structure`, `gain_stage_tracks`, `fix_resonances`, `match_loudness`, `auto_mix_pass` y `compare_to_reference`.

#### macOS, con BlackHole (gratuito)
1. Instala **BlackHole 2ch**: https://existential.audio/blackhole/ o con `brew install blackhole-2ch`.
2. Abre **Configuración de Audio MIDI** y pulsa **+ → Crear dispositivo de salida múltiple**:
   * Marca tu interfaz o los altavoces **y** BlackHole 2ch.
   * Pon tu interfaz como *dispositivo principal*.
   * Activa *Corrección de deriva* (drift correction) en BlackHole.
3. En **Cubase**, ve a **Studio → Studio Setup → Audio System** y elige como driver el **dispositivo de salida múltiple**.
4. En **Studio → Audio Connections → Outputs**, el *Stereo Out* debe ir a los canales 1-2 de ese dispositivo.
5. Comprueba que el servidor ve **BlackHole 2ch** como entrada. Pregunta a Claude: *"lista mis entradas de audio"*, que usa `list_audio_inputs`. BlackHole aparece marcado como loopback y se usa por defecto.

> **Alternativa con menos latencia (Cubase Pro y dispositivo agregado)**:
> 1. Crea un *Dispositivo agregado* con tu interfaz y BlackHole, y úsalo como driver.
> 2. En **Audio Connections → Control Room**, añade un *Monitor* asignado a los canales de BlackHole, o usa *Studio Sends* para enviar la mezcla a esas salidas.

#### Windows
En Windows, Cubase usa ASIO, que suele tener la tarjeta en exclusiva. Hay tres opciones, de la más sencilla a la más compleja:

**A) Tu interfaz tiene loopback.** Muchas lo incluyen: Steinberg UR (función *Loopback* en dspMixFx), Focusrite Scarlett de 3.ª/4.ª generación (entradas *Loopback 1-2*), RME (TotalMix *Loopback*), MOTU, Audient EVO, etc.
1. Activa el loopback en el software de la interfaz.
2. Fija ese dispositivo con `CUBASE_MCP_LISTEN_DEVICE` (por ejemplo, `"Loopback"`).

**B) VoiceMeeter Banana (gratuito)**: https://vb-audio.com/Voicemeeter/banana.htm
1. Selecciona **Voicemeeter Virtual ASIO** como driver de Cubase.
2. En VoiceMeeter, envía la entrada virtual a **A1**, que es tu interfaz, y a **B1**.
3. El servidor escucha `Voicemeeter Out B1` (`CUBASE_MCP_LISTEN_DEVICE="Voicemeeter Out B1"`).

**C) VB-Cable con el *Generic Low Latency ASIO Driver* de Cubase**: https://vb-audio.com/Cable/
1. En *Studio Setup → Generic Low Latency ASIO Driver*, activa tu tarjeta y **CABLE Input** como salidas.
2. En **Audio Connections → Outputs**, crea un bus estéreo hacia CABLE Input y activa allí el Stereo Out. En Pro también puedes usar Control Room.
3. El servidor escucha `CABLE Output`.

#### Sin loopback: usar un archivo
`File → Export → Audio Mixdown` (también `project_action('export_mixdown')`). Después pide: *"analiza el archivo …/Mixdown.wav"*, que usa `analyze_audio_file`. Así se tienen todas las funciones de análisis, aunque sin el bucle automático.

### Nivel 5: el micrófono (tararear → notas)
`transcribe_audio` usa la entrada predeterminada del sistema o la que indiques con `device`. En macOS, la primera vez el sistema pide permiso de micrófono para la aplicación que lanza el servidor (Claude o Terminal). Para obtener notas precisas, canta o toca **una sola voz**, con el metrónomo de Cubase sonando.

## 7. Comprobación rápida

Pide a Claude, en orden:
1. *"Estado de la conexión con Cubase"* (`connection_status`): debe indicar el script, el número de controles y si DirectAccess está disponible.
2. *"Pon el tempo a 100 y dale a play"*.
3. *"Escucha los compases 1 a 8"* (`listen_to_cubase`): debe devolver LUFS, la tonalidad y los acordes.
4. *"Enséñame el espectrograma"* (`show_spectrogram`).

## 8. Variables de entorno

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `CUBASE_MCP_LISTEN_DEVICE` | auto (busca un loopback) | Dispositivo de audio que escucha el servidor (nombre parcial o id) |
| `CUBASE_MCP_EXPORT_DIR` | `~/Documents/CubaseMCP` | Dónde se guardan los WAV escuchados y los MIDI exportados |
| `CUBASE_MCP_WRITE_MODE` | `nrpn` | `sysex` si los controles no responden a NRPN |
| `CUBASE_MCP_RECORD_LATENCY_MS` | `0` | Compensación de tiempo al grabar notas |
| `CUBASE_MCP_PORT_TO_CUBASE` / `_FROM_CUBASE` / `_NOTES` | nombres estándar | Nombres alternativos de los puertos |
| `CUBASE_MCP_LOG` | `INFO` | Nivel de log (`DEBUG` para diagnosticar) |

## 9. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| "timeout waiting for Cubase" | El script no está activo o los puertos están mal asignados | Pulsa Reload Scripts, revisa el MIDI Remote Manager y comprueba que los puertos coinciden (apartado 3) |
| Claude lee el proyecto, pero los cambios no se aplican | Cubase no procesa el NRPN | Pon `CUBASE_MCP_WRITE_MODE=sysex` y reinicia Claude |
| "cannot open MIDI ports" en Windows | Faltan los puertos de loopMIDI | Créalos con los nombres exactos |
| `listen_to_cubase` dice "silence captured" | La salida de Cubase no llega al loopback | Revisa el apartado 6 y prueba con `listen` mientras suena algo |
| Las notas grabadas empiezan tarde o pronto | Precount activado o latencia | Desactiva el precount y ajusta `CUBASE_MCP_RECORD_LATENCY_MS` |
| Las notas no se graban | La entrada de la pista no recibe `CubaseMCP Notes` | Pon la entrada de la pista en All MIDI Inputs o en CubaseMCP Notes |
| `load_plugin` falla | Cubase anterior a 15.0.20 | Carga el plug-in a mano; los parámetros sí son accesibles desde 13.0.50 |
| Faltan pistas en la lista | El proyecto tiene más de 32 canales | Usa `list_tracks(page=1)` o `bank('next')` |
| Los volúmenes en dB no son exactos | Se usa la curva del fader | El servidor ajusta iterativamente; la tolerancia es de 0,1 dB |

## 10. Qué no se puede hacer (límites de la API de Steinberg)

* Renombrar pistas o marcadores, y escribir o borrar notas concretas dentro de una parte.
* Leer la lista de marcadores, los eventos o las partes del proyecto.
* Algunos comandos abren diálogos que requieren un clic: Add Track → Instrument, Import MIDI, Export Mixdown.

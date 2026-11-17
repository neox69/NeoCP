# Develop NeoCP

## On macOS, with just

The `justfile` supports macOS only. You need [just](https://just.systems/), [uv](https://docs.astral.sh/uv/) and Node.js 20 or later.

1. Check the tools:

   ```bash
   just check
   ```

2. Run the tests. The recipe creates `.venv` with Python 3.12 first.

   ```bash
   just test
   ```

3. Make sure that the generated MIDI Remote script matches the template:

   ```bash
   just check-script
   ```

Run `just` to list all recipes.

## On other platforms

1. Install the package in editable mode, with the development dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

2. After you change `midi_remote/NeoCP_Bridge.template.js` or the command catalog, regenerate the script:

   ```bash
   python scripts/build_midi_remote.py
   ```

   Do not edit `neocp/midi_remote/NeoCP/Bridge/NeoCP_Bridge.js` by hand. CI fails if the generated file differs from the template.

3. Run the tests. They need Node.js.

   ```bash
   pytest
   ```

The tests run the real script in a simulated Cubase. [Validation strategy](../explanation/validation-strategy.md) explains what this proves and what it does not prove. To validate in a real Cubase, follow [Validate NeoCP against a real Cubase](validate-against-cubase.md).

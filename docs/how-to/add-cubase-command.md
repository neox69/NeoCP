# Add a Cubase command

The bundled command catalog comes from Cubase 13 to 15. Use this procedure for a command that is not in the catalog, for example a new Cubase 16 command such as stem separation, Strum, Stretch Selection or Instrument Stacks.

1. In Cubase, open **Edit > Key Commands**. Find the category and the exact name of the command.
2. Run:

   ```bash
   neocp-install --add-command "<Category>/<Command>"
   ```

   Or ask Claude to use `bind_new_command` with the category and the command.

3. In the MIDI Remote tab, press **Reload Scripts**.
4. Ask Claude to use `reconnect`.

NeoCP stores the added commands in `~/.neocp/extra_commands.json`. They stay after you reinstall NeoCP.

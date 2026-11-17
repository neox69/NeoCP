# Connect Claude to NeoCP

The installer does this step for you. It registers the server under the name `neocp`.

## Claude Desktop

Edit `claude_desktop_config.json`:

* On macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
* On Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "neocp": {
      "command": "neocp",
      "env": {
        "NEOCP_LISTEN_DEVICE": "BlackHole 2ch"
      }
    }
  }
}
```

The `env` block is optional. It selects the audio device for listening.

## Claude Code

```bash
claude mcp add neocp -e NEOCP_LISTEN_DEVICE="BlackHole 2ch" -- neocp
```

## Test the connection

Ask Claude: "What is in my Cubase project?" Claude must list your tracks and the tempo.

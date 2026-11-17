# NeoCP – AI co-producer for Cubase

English · [Español](README.md)

NeoCP is an MCP (Model Context Protocol) server for Steinberg Cubase and Nuendo 12 and later. It lets Claude, or any other MCP client, read and control a Cubase project while it runs.

NeoCP can:

* control the transport, the tempo and the locators;
* control the full mixer and the channel strip;
* read and set insert effects and plug-in parameters;
* run more than 1400 Cubase commands;
* generate music and record notes on tracks;
* listen to the mix and analyze it.

> **Status: not validated in a real Cubase.** All automated tests pass, but they run against a simulated Cubase. The [validation matrix](docs/reference/validation-matrix.md) records what is tested in a real Cubase. The [validation strategy](docs/explanation/validation-strategy.md) explains why.

## How it works

A Python MCP server runs on your computer. A MIDI Remote script runs inside Cubase. The two parts talk through three MIDI ports. The [architecture](docs/explanation/architecture.md) explains the details.

## Get started

* New users: follow [Your first session with NeoCP](docs/tutorials/first-session.md).
* To install: follow [Install NeoCP](docs/how-to/install.md).
* For the list of all 87 tools: read the [tools reference](docs/reference/tools.md).

The [documentation index](docs/README.md) lists all pages.

## Development

Follow [Develop NeoCP](docs/how-to/develop.md).

## License

MIT.

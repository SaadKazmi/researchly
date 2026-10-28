# Researchly

Open-source, local-first, multi-agent research assistant. Chat about a topic and Researchly's agents:

- search open scholarly sources;
- rank the best papers, explaining each score;
- build citation and concept graphs you can explore;
- explain and visualize concepts;
- write research notebooks and books with checked citations.

The only thing that costs money is your own LLM API key. Everything else is free, keyless and installed for you.

> **Status:** under active development. The install commands below work from the first tagged release.

## Install

macOS / Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/SaadKazmi/researchly/main/install.sh | bash
```

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/SaadKazmi/researchly/main/install.ps1 | iex"
```

Then run `researchly start`. Researchly uses Docker if you have a container engine (it starts the engine for you) and runs natively otherwise. No admin rights are needed.

## Documentation

- Design spec: [docs/superpowers/specs/2026-10-28-researchly-design.md](docs/superpowers/specs/2026-10-28-researchly-design.md)
- Contributor and agent guide: [AGENTS.md](AGENTS.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

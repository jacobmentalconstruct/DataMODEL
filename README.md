# DataMODEL: a project skeleton for working with AI agents

Unpack this into an empty folder, a Python project, or a pile of old files, then point an
agent (Claude Code or anything that reads `AGENTS.md`) at it. You get two things:

- **A way of working**: the agent orients before proposing, proposes before building, and
  plans with you in small, verified steps. It won't invent your project for you.
- **Local tools** that make the agent's view of the project cheap and exact: project map,
  symbol outlines, import graphs, search, data-file schemas, "what changed since last
  time", a decision journal, and a runner for local Ollama models. Everything runs on your
  machine, needs only Python's standard library, and records what it did.

## What's in it

| Path | What it is |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | Where agents start. `CLAUDE.md` just points Claude Code at `AGENTS.md`. |
| `PROJECT.md` | What you are building. Starts blank; filled in with the agent. |
| `PLAN.md` | How you'll get there, and whether building is allowed yet. |
| `.framework/` | The reusable rules: design principles, architecture, workflow. Same in every project. |
| `.tools/` | The local tools, plus an MCP server that `.mcp.json` registers. See `.tools/README.md`. |
| `.dev/` | Only in this repository: the skeleton's own definition, plan and benchmark. `pack` leaves it out. |

## Start a project

You need Python 3.10 or newer.

1. Get a copy: unpack an archive made with `pack` (below), or clone this repository and
   delete its `.dev/` folder, which holds the skeleton's own plan, not yours.
2. Open the folder with your agent. Claude Code asks once to approve the `helpers` tool
   server; say yes. The tools set themselves up on first use.
3. Tell the agent what you want to do. It starts from `AGENTS.md`.

Optional: `pyarrow` lets the `schema` tool read Parquet/Feather files. A local
[Ollama](https://ollama.com) enables the `ollama` tool.

## Pack and unpack

To reuse this skeleton (or snapshot any project built on it) as a single archive:

- **Pack**: double-click `.tools/pack.bat` (Windows), or run `sh .tools/pack.sh` (macOS/Linux).
  This writes `<ProjectName>.zip` beside the project folder, plus two unpack launchers.
  Add a path to choose where: `.tools/pack.bat D:\archive\name.zip`.
- **Unpack**: double-click `<ProjectName>-unpack.bat` (Windows), or run
  `sh <ProjectName>-unpack.sh` (macOS/Linux). Pick a folder in the dialog that opens. Or
  run `python <ProjectName>.zip <folder>` directly. Files already in the folder are
  never overwritten.

Pack rather than zipping by hand: packing leaves out the copy's private state (its
identity, tool history, journal and snapshots), your local agent settings
(`.claude/settings.local.json`) and `.dev/`, so every project starts clean.

## Large folders

The tools handle big trees, but change tracking keeps a small record per file. For
folders with hundreds of thousands of files (document dumps, datasets), list them in
`.tools/snapshot-ignore` (gitignore syntax, e.g. `raw_pdfs/`). They are then tracked as
folders only. Search, listing and schema tools still see inside them.

## License

MIT. See `LICENSE.md`.

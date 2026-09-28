# Agents

Start with `.framework/AGENT-START-HERE.md`: it defines how this project is oriented, planned, and worked on with the user.

- `PROJECT.md`: what is being built. `PLAN.md`: the route, the current decision, and implementation permission.
- If `.dev/` exists, this is the skeleton's own repository: `.dev/PROJECT.md` and `.dev/PLAN.md` govern its development, and the root `PROJECT.md`/`PLAN.md` are templates to leave blank.
- `.tools/`: local, governed project tools (MCP server `helpers`, or `python .tools/bin/helpers.py <tool> key=value`). Start with `map` for a project you haven't seen, or `changes` when returning. `.tools/README.md` lists every tool.

Orient before proposing. Propose before implementing. Plan with the user rather than for the user.

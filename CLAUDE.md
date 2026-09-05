@AGENTS.md

# Delta for Claude Code

`AGENTS.md` is the canonical instruction file for every agent (Pi, Codex, Claude Code). Conventions change
there, not here. This file only says what is specific to Claude Code.

- Subagents: `.claude/agents/` (`motus-verifier`, `motus-implementer`, `motus-issue-auditor`,
  `motus-adversary`). The same four exist for Pi in `.pi/agents/`, generated from these files by
  `.pi/sync-agents.py`: edit here, then run the script.
- Skills: `.claude/skills/` (`adr`, `adversarial-round`, `release`). `.pi/skills/` are symlinks to them.
- `release` creates public objects and requires founder authorisation; never invoke it on your own.

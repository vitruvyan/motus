#!/usr/bin/env python3
"""Regenerate .pi/agents/*.md from .claude/agents/*.md: same body, Pi frontmatter (subscription models).
Run after editing a Claude agent definition. The Claude file is the source; this one is derived."""
import re, pathlib
MODELS = {'motus-adversary': ('openai-codex/gpt-5.5', 'high', 'read, grep, find, ls, bash, write, edit'),
          'motus-implementer': ('openai-codex/gpt-5.6-luna', 'medium', 'read, grep, find, ls, bash, write, edit'),
          'motus-verifier': ('openai-codex/gpt-5.6-luna', 'low', 'read, grep, find, ls, bash'),
          'motus-issue-auditor': ('openai-codex/gpt-5.6-luna', 'low', 'read, grep, find, ls, bash')}
root = pathlib.Path(__file__).resolve().parents[1]
for src in sorted((root / '.claude/agents').glob('*.md')):
    fm, body = re.match(r'---\n(.*?)\n---\n(.*)', src.read_text(), re.S).groups()
    name = re.search(r'^name:\s*(.+)$', fm, re.M).group(1).strip()
    desc = re.search(r'^description:\s*(.+)$', fm, re.M).group(1).strip()
    model, think, tools = MODELS[name]
    (root / '.pi/agents' / src.name).write_text(
        f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\nthinking: {think}\ntools: {tools}\nmaxSubagentDepth: 0\n---\n"
        f"<!-- Body shared with .claude/agents/{src.name}: edit there and re-run .pi/sync-agents.py. Under pi-herdr, the adversary and the architect run as the `claude` Herdr kind (Claude Max); this file is the pi-subagents form. -->\n{body}")
    print('synced', src.name)

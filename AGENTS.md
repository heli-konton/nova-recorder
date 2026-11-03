# Agent instructions

Part of the NovaHQ lab.

## Git workflow (NovaHQ lab convention)
- `main` is the tested baseline (tagged `baseline-2026-10-31`). Never push to it directly.
- Branch per change, named by who is working: `hermes/<topic>` (Heli, as `heli-konton`), `claude/<topic>` and `codex/<topic>` (Claude and Codex, as `konton-developers`). Dan (`Beardsman69`) approves every PR.
- Raise a PR, test, then merge. Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- Anything touched outside the lab is branched from the heli-konton repo, PR'd, tested, then merged.
- Never commit secrets, tokens, vault content or generated media.

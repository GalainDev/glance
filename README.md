# glance

A status line for [Claude Code](https://code.claude.com): everything you need
in one glance under the prompt. Context window, 5-hour and weekly limits, how
long until your prompt cache goes cold, where you are and what's running.

```
ctx ███░░░░░░░ 28%  │  5h █████░░░ 68% ↻1h40m  │  7d █░░░░░░░ 9% ↻1d9h
📁 ~/code/app  🌿 main*  🌳 feature-x
🧠 Opus 5.5  Pro  ⚡ high  │  cache 52m  │  sid: 7f3a9c21
```

- **Gauges first.** The row right under the input box is the one that changes
  every turn. Bars go green below 60 %, yellow below 85 %, red above, with a
  countdown to each limit's reset.
- **Where you are.** Full path, branch (`*` dirty, `↑2 ↓1` ahead/behind),
  worktree and open PR. `none` when there isn't one.
- **What's running.** Model, plan, effort level, prompt-cache countdown
  (yellow in the last 5 minutes, red `cold 275k` once it has expired: what your
  next message will re-process), and a short session id.
- **Never blank.** Missing fields are skipped, bad input still renders, slow
  git is cut off after 1 s, and segments drop by priority to fit the pane.

One Python 3 file, standard library only. Nothing leaves your machine.

## Install

```sh
git clone https://github.com/GalainDev/glance ~/glance
```

Add to `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/glance/statusline.py",
  "refreshInterval": 30
}
```

`refreshInterval` keeps the countdowns moving while you're idle. Rate-limit
gauges appear for Pro and Max plans after the first reply in a session.

Optionally put the preferences CLI on your `PATH`:

```sh
ln -s ~/glance/statusline.py ~/.local/bin/statusline
```

## Preferences

Run from a terminal, or inside Claude Code with a `!` prefix
(`! statusline emoji`). Changes apply on the next refresh, no restart.

| Command | Effect |
|---|---|
| `statusline` | current settings and a preview |
| `statusline preview` | your last real session drawn in every style |
| `statusline emoji [on\|off]` | emoji labels on the gauges |
| `statusline compact [on\|off]` | everything on one row |
| `statusline spacing [on\|off]` | blank rows between rows |
| `statusline bars <style>` | `block` (default), `pill`, `dots`, `line` |
| `statusline hide\|show <segment>…` | `name`, `duration`, `lines`, `cost`, `thinking` start hidden |

Preferences live in `$XDG_CONFIG_HOME/claude-statusline/prefs.json` (default
`~/.config/…`) and store only your changes, so new defaults still reach you.
Environment overrides: `STATUSLINE_EMOJI`, `STATUSLINE_COMPACT`,
`STATUSLINE_SPACING` (`0`/`1`), `STATUSLINE_BARS`, `NO_COLOR`.

If Claude Code's Bash sandbox is on, allow the prefs folder so the `!`
commands can save:

```json
"sandbox": { "filesystem": { "allowWrite": ["~/.config/claude-statusline"] } }
```

## Codex

Codex's footer can't run a script, only a fixed list of built-in items. The
closest match, in `~/.codex/config.toml`:

```toml
[tui]
status_line = ["context-used", "five-hour-limit", "weekly-limit", "current-dir", "git-branch", "model-with-reasoning"]
```

## Notes

- The plan name (`Pro`, `Max 20x`) comes from Claude Code's own account cache in
  `~/.claude.json`, which is undocumented. If it changes, that segment just
  disappears.
- Blank spacer rows use U+2800 (braille blank), because Claude Code drops
  whitespace-only lines.

## Tests

```sh
python3 -m unittest discover -s tests
```

## License

MIT

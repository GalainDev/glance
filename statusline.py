#!/usr/bin/env python3
"""glance — Claude Code status line: colour-coded rows for limits, folder and model
(plus an opt-in session row: id, name, duration, lines changed).

Claude Code runs this with the session JSON on stdin (settings.json
`statusLine`). Run it from a terminal to change preferences:

  statusline                    show preferences and a preview
  statusline on|off             show or hide the whole status line
  statusline emoji [on|off]     emoji labels (toggles without an argument)
  statusline compact [on|off]   one row instead of three
  statusline spacing [on|off]   blank rows between rows and above the footer
  statusline hide|show <seg>    hide or show a segment (thinking, sid, name,
                                duration, lines, cost start hidden)
  statusline bars <style>       gauge style: block (default), pill, dots, line
  statusline preview            render the last real input in every style

Preferences live in $XDG_CONFIG_HOME/claude-statusline/prefs.json and apply on
the next refresh. Env overrides: STATUSLINE_EMOJI, _COMPACT, _SPACING (0|1),
NO_COLOR. Stdlib only (Python 3.9); render mode never exits non-zero.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

SEGMENTS = (
    "dir", "branch", "worktree", "pr", "model", "tier", "effort", "thinking",
    "fast", "cache", "ctx", "5h", "7d", "sid", "name", "duration", "lines", "cost",
    "style", "agent",
)
DEFAULT_PREFS = {"enabled": True, "emoji": False, "compact": False, "spacing": False, "bars": "block",
                 "hide": [], "show": []}
TOGGLES = ("enabled", "emoji", "compact", "spacing")
# Hidden unless `statusline show <seg>`. Prefs store only the user's changes
# against this set, so changing a default later still takes effect.
DEFAULT_HIDDEN = ("thinking", "name", "duration", "lines", "cost")
COMPACT = {"dir", "branch", "model", "effort", "ctx", "5h", "7d"}

# Gauge glyphs (filled, empty). All single-column in non-CJK terminals.
BAR_STYLES = {"block": ("█", "░"), "pill": ("▰", "▱"), "dots": ("●", "○"),
              "line": ("━", "━")}

# segment: (text label, emoji label). Emoji are default-emoji-presentation
# code points (no U+FE0F) so terminals agree on their two-column width.
LABELS = {
    "dir": ("📁", "📁"), "branch": ("🌿", "🌿"), "worktree": ("🌳", "🌳"),
    "pr": ("", "🔀"), "model": ("🧠", "🧠"), "tier": ("", "💳"),
    "effort": ("⚡", "⚡"), "thinking": ("thinking", "💭"), "fast": ("fast", "🚀"),
    "ctx": ("ctx", "📊"), "5h": ("5h", "⏳ 5h"), "7d": ("7d", "📅 7d"),
    "sid": ("sid:", "sid:"), "cache": ("cache", "💾"), "name": ("", "💬"), "duration": ("", "⌛"),
    "lines": ("", "📝"), "cost": ("", "💰"), "style": ("style", "🎨"),
    "agent": ("agent", "🤖"),
}

TIERS = {"claude_pro": "Pro", "claude_max": "Max", "claude_team": "Team",
         "claude_enterprise": "Enterprise"}

SAMPLE = {
    "session_id": "00000000-0000-4000-8000-000000000000",
    "session_name": "sample session",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "workspace": {"current_dir": os.path.expanduser("~/developer/dotfiles")},
    "cost": {"total_cost_usd": 1.8, "total_duration_ms": 754000,
             "total_lines_added": 120, "total_lines_removed": 30},
    "context_window": {"used_percentage": 42, "total_input_tokens": 84000,
                       "context_window_size": 200000},
    "effort": {"level": "high"}, "thinking": {"enabled": True},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": time.time() + 8040},
        "seven_day": {"used_percentage": 88, "resets_at": time.time() + 274000},
    },
}

SEP = "  │  "
# Spacer row between rows. Claude Code drops whitespace-only lines, so use
# U+2800 BRAILLE PATTERN BLANK: it draws as blank but isn't whitespace.
SPACER = "⠀"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x07]*\x07")
GIT_TTL = 5.0
GIT_TIMEOUT = 1.0


# ── paths and preferences ────────────────────────────────────────────────────

def _xdg(var, fallback):
    return os.path.join(os.environ.get(var) or os.path.expanduser(fallback),
                        "claude-statusline")


def config_dir():
    return _xdg("XDG_CONFIG_HOME", "~/.config")


def cache_dir():
    return _xdg("XDG_CACHE_HOME", "~/.cache")


def prefs_path():
    return os.path.join(config_dir(), "prefs.json")


def load_prefs(apply_env=True):
    prefs = dict(DEFAULT_PREFS, hide=[], show=[])
    try:
        with open(prefs_path()) as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in TOGGLES:
                if isinstance(data.get(key), bool):
                    prefs[key] = data[key]
            if data.get("bars") in BAR_STYLES:
                prefs["bars"] = data["bars"]
            for key in ("hide", "show"):
                if isinstance(data.get(key), list):
                    prefs[key] = [s for s in data[key] if s in SEGMENTS]
    except (OSError, ValueError):
        pass
    if apply_env:
        for key in TOGGLES:
            value = os.environ.get("STATUSLINE_" + key.upper())
            if value in ("0", "1"):
                prefs[key] = value == "1"
        if os.environ.get("STATUSLINE_BARS") in BAR_STYLES:
            prefs["bars"] = os.environ["STATUSLINE_BARS"]
    return prefs


def hidden_segments(prefs):
    return (set(DEFAULT_HIDDEN) | set(prefs["hide"])) - set(prefs["show"])


def save_prefs(prefs):
    os.makedirs(config_dir(), exist_ok=True)
    tmp = prefs_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(prefs, f, indent=2)
        f.write("\n")
    os.replace(tmp, prefs_path())


# ── small formatters ─────────────────────────────────────────────────────────

def get(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tokens(n):
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1000:
        return "%dk" % round(n / 1000)
    return str(int(n))


def countdown(seconds):
    seconds = int(seconds)
    if seconds <= 0:
        return "now"
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return "%dd%dh" % (days, hours)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    return "%dm" % max(minutes, 1)


def duration(ms):
    minutes = int(ms // 60000)
    if minutes < 1:
        return "<1m"
    hours, minutes = divmod(minutes, 60)
    return "%dh%02dm" % (hours, minutes) if hours else "%dm" % minutes


def display_path(path, home):
    path = path.rstrip(os.sep) or os.sep
    home = (home or "").rstrip(os.sep)
    if home and (path == home or path.startswith(home + os.sep)):
        path = "~" + path[len(home):]
    return path


def truncate(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def vwidth(text):
    width = 0
    for ch in ANSI_RE.sub("", text):
        if unicodedata.combining(ch) or ch in "\u200d\ufe0e\ufe0f":
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


class Paint:
    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, code, text):
        return "\x1b[%sm%s\x1b[0m" % (code, text) if self.enabled and text else text

    def link(self, url, text):
        return "\x1b]8;;%s\x07%s\x1b]8;;\x07" % (url, text) if self.enabled else text


def level_colour(pct):
    return "32" if pct < 60 else "33" if pct < 85 else "31"


def bar(paint, pct, width=8, style="block"):
    full, empty = BAR_STYLES.get(style, BAR_STYLES["block"])
    filled = max(0, min(width, int(round(pct / 100.0 * width))))
    return paint(level_colour(pct), full * filled) + paint("2", empty * (width - filled))


# ── external facts: git and plan tier ────────────────────────────────────────

def parse_git_status(out):
    info = {"branch": None, "ahead": 0, "behind": 0, "dirty": False}
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):]
            info["branch"] = None if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            match = re.match(r"# branch\.ab \+(\d+) -(\d+)", line)
            if match:
                info["ahead"], info["behind"] = int(match.group(1)), int(match.group(2))
        elif line and not line.startswith("#"):
            info["dirty"] = True
    return info


def git_info(cwd):
    """Branch/dirty/ahead/behind for cwd, cached briefly; None outside a repo."""
    cache = os.path.join(cache_dir(), "git-%s.json" % hashlib.sha1(cwd.encode()).hexdigest()[:12])
    try:
        if time.time() - os.stat(cache).st_mtime < GIT_TTL:
            with open(cache) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v2", "--branch"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {"branch": None, "slow": True}
    info = parse_git_status(proc.stdout) if proc.returncode == 0 else None
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(cache, "w") as f:
            json.dump(info, f)
    except OSError:
        pass
    return info


def plan_tier():
    """Plan name from Claude Code's own account cache; None if unknown.

    `oauthAccount` in ~/.claude.json is undocumented, so any surprise here
    just hides the segment.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    try:
        with open(os.path.join(base, ".claude.json")) as f:
            account = json.load(f).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(account, dict):
        return None
    org = account.get("organizationType") or ""
    name = TIERS.get(org) or (org[len("claude_"):].title() if org.startswith("claude_") else None)
    limit = "%s %s" % (account.get("userRateLimitTier") or "", account.get("organizationRateLimitTier") or "")
    multiple = re.search(r"max_(\d+)x", limit)
    if name == "Max" and multiple:
        name = "Max %sx" % multiple.group(1)
    return name


# ── rendering ────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self, data, prefs, colour, now=None):
        self.d = data if isinstance(data, dict) else {}
        self.prefs = prefs
        self.p = Paint(colour)
        self.now = time.time() if now is None else now
        self.hidden = hidden_segments(prefs)

    def label(self, seg, value):
        text, emoji = LABELS[seg]
        prefix = emoji if self.prefs["emoji"] else text
        if not value:
            return prefix
        return "%s %s" % (prefix, value) if prefix else value

    # Each builder returns a list of (segment, priority, text); lower priority
    # survives longer when the row is too wide.

    def where(self):
        p, d = self.p, self.d
        out = []
        cwd = get(d, "workspace", "current_dir") or get(d, "cwd") or os.getcwd()
        out.append(("dir", 0, self.label("dir", p("36", display_path(cwd, os.path.expanduser("~"))))))
        git = git_info(cwd) if os.path.isdir(cwd) else None
        if git and git.get("branch"):
            text = p("35", git["branch"])
            if git.get("dirty"):
                text += p("33", "*")
            if git.get("ahead"):
                text += p("2", " ↑%d" % git["ahead"])
            if git.get("behind"):
                text += p("2", " ↓%d" % git["behind"])
            out.append(("branch", 1, self.label("branch", text)))
        elif git and git.get("slow"):
            out.append(("branch", 1, self.label("branch", p("2", "git…"))))
        elif git is not None:  # in a repo with HEAD detached
            out.append(("branch", 1, self.label("branch", p("2", "detached"))))
        else:  # placeholders drop first when the row is too wide
            out.append(("branch", 6, self.label("branch", p("2", "none"))))
        worktree = get(d, "worktree", "name") or get(d, "workspace", "git_worktree")
        if worktree:
            out.append(("worktree", 2, self.label("worktree", p("32", worktree))))
        else:
            out.append(("worktree", 6, self.label("worktree", p("2", "none"))))
        pr = num(get(d, "pr", "number"))
        if pr:
            text = "%s #%d" % ("MR" if get(d, "pr", "kind") == "mr" else "PR", pr)
            url = get(d, "pr", "url")
            out.append(("pr", 5, self.label("pr", p.link(url, text) if url else text)))
        return out

    def who(self):
        p, d = self.p, self.d
        out = []
        model = get(d, "model", "display_name") or get(d, "model", "id")
        if model:
            out.append(("model", 0, self.label("model", p("1", model))))
        if "tier" not in self.hidden:
            tier = plan_tier()
            if tier:
                out.append(("tier", 4, self.label("tier", p("34", tier))))
        effort = get(d, "effort", "level")
        if effort:
            out.append(("effort", 2, self.label("effort", effort)))
        if get(d, "thinking", "enabled") is True:
            out.append(("thinking", 3, self.label("thinking", "")))
        if get(d, "fast_mode") is True:
            out.append(("fast", 3, self.label("fast", "")))
        return out

    def ctx(self, with_bar=True):
        p, d = self.p, self.d
        pct = num(get(d, "context_window", "used_percentage"))
        if pct is None:
            return [("ctx", 0, self.label("ctx", p("2", "—")))]
        text = p(level_colour(pct), "%d%%" % round(pct))
        if with_bar:
            text = bar(p, pct, 10, self.prefs["bars"]) + " " + text
        return [("ctx", 0, self.label("ctx", text))]

    def limits(self, with_bar=True):
        p, d = self.p, self.d
        out = []
        for seg, key, prio in (("5h", "five_hour", 1), ("7d", "seven_day", 2)):
            pct = num(get(d, "rate_limits", key, "used_percentage"))
            if pct is None:
                continue
            text = p(level_colour(pct), "%d%%" % round(pct))
            if with_bar:
                text = bar(p, pct, 8, self.prefs["bars"]) + " " + text
                resets = num(get(d, "rate_limits", key, "resets_at"))
                if resets:
                    text += p("2", " ↻%s" % countdown(resets - self.now))
            out.append((seg, prio, self.label(seg, text)))
        return out

    def session(self):
        p, d = self.p, self.d
        out = []
        name = get(d, "session_name")
        if isinstance(name, str) and name:
            out.append(("name", 2, self.label("name", truncate(name, 28))))
        ms = num(get(d, "cost", "total_duration_ms"))
        if ms:
            out.append(("duration", 3, self.label("duration", duration(ms))))
        added = num(get(d, "cost", "total_lines_added")) or 0
        removed = num(get(d, "cost", "total_lines_removed")) or 0
        if added or removed:
            out.append(("lines", 4, self.label("lines", p("32", "+%d" % added) + " " + p("31", "−%d" % removed))))
        usd = num(get(d, "cost", "total_cost_usd"))
        if usd is not None:
            out.append(("cost", 4, self.label("cost", "~$%.2f" % usd)))
        style = get(d, "output_style", "name")
        if style and style != "default":
            out.append(("style", 5, self.label("style", style)))
        agent = get(d, "agent", "name")
        if agent:
            out.append(("agent", 3, self.label("agent", agent)))
        return out

    def row(self, groups, width, sep=" │ "):
        """Join groups of segments, dropping low-priority ones to fit width."""
        groups = [[s for s in g if s[0] not in self.hidden] for g in groups]
        while True:
            line = self.p("2", sep).join(
                "  ".join(s[2] for s in g) for g in groups if g)
            if not width or vwidth(line) <= width:
                return line
            candidates = [(s[1], gi, si) for gi, g in enumerate(groups)
                          for si, s in enumerate(g) if s[1] > 0]
            if not candidates:
                return line
            _, gi, si = max(candidates)
            del groups[gi][si]

    def cache(self):
        """Prompt cache: time until it goes cold, or what a cold start re-reads."""
        pc = get(self.d, "prompt_cache")
        if not isinstance(pc, dict) or not pc.get("caching_observed"):
            return []
        left = (num(pc.get("expires_at")) or 0) - self.now
        if pc.get("warm") is True and left > 0:
            text = self.p("32" if left >= 300 else "33", countdown(left))
        else:
            rebuild = num(pc.get("recache_tokens_if_cold"))
            text = self.p("31", "cold" + (" %s" % tokens(rebuild) if rebuild else ""))
        return [("cache", 2, self.label("cache", text))]

    def sid(self):
        sid = get(self.d, "session_id")
        if isinstance(sid, str) and sid:
            return [("sid", 3, self.label("sid", self.p("2", sid[:8])))]
        return []

    def render(self, width=0):
        if self.prefs["compact"]:
            def keep(segs):
                return [s for s in segs if s[0] in COMPACT]
            return self.row([keep(self.where()), keep(self.who()),
                             self.ctx(False) + self.limits(False)], width)
        # Gauges first: the row nearest the input box changes every turn.
        # Then location, then model, which rarely changes in a session.
        lines = [
            self.row([self.ctx()] + [[s] for s in self.limits()], width, sep=SEP),
            self.row([self.where()], width),
            self.row([self.who(), self.cache(), self.sid()], width, sep=SEP),
            self.row([self.session()], width),
        ]
        rows = [line for line in lines if line]
        if not self.prefs["spacing"]:
            return "\n".join(rows)
        # Spacer rows between rows and one above Claude Code's footer.
        return ("\n%s\n" % SPACER).join(rows) + "\n" + SPACER


def terminal_width():
    try:
        return max(0, int(os.environ.get("COLUMNS", "0")) - 4)
    except ValueError:
        return 0


def colour_enabled():
    return "NO_COLOR" not in os.environ


def cli_colour():
    """Previews use colour only on a real terminal; Claude Code's `!` output
    pane shows escape codes as text."""
    return colour_enabled() and sys.stdout.isatty()


def render_stdin(raw):
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if isinstance(data, dict) and data:
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            with open(os.path.join(cache_dir(), "last.json"), "w") as f:
                f.write(raw)
        except OSError:
            pass
    prefs = load_prefs()
    if not prefs["enabled"]:
        return  # no output: Claude Code hides the status line
    print(Renderer(data, prefs, colour_enabled()).render(terminal_width()))


# ── preferences CLI ──────────────────────────────────────────────────────────

def last_input():
    try:
        with open(os.path.join(cache_dir(), "last.json")) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data, "last real session input"
    except (OSError, ValueError):
        pass
    return SAMPLE, "sample input (no real session seen yet)"


def preview(all_styles):
    data, source = last_input()
    width = terminal_width() or 100
    prefs = load_prefs()
    styles = [("current", prefs)]
    if all_styles:
        styles = [(name, dict(prefs, emoji=e, compact=c)) for name, e, c in (
            ("text", False, False), ("emoji", True, False),
            ("compact", False, True), ("compact + emoji", True, True))]
    print("Preview from %s:\n" % source)
    for name, style in styles:
        print("── %s ──" % name)
        print(Renderer(data, style, cli_colour()).render(width))
        print()
    if all_styles:
        print("── bars ──")
        for name in BAR_STYLES:
            row = Renderer(data, dict(prefs, compact=False, bars=name), cli_colour()).render(width)
            gauges = [l for l in row.split("\n") if "ctx" in ANSI_RE.sub("", l)]
            print("%-6s %s" % (name, gauges[0] if gauges else row))


def show_status():
    prefs = load_prefs(apply_env=False)
    print("bar:     %s" % ("on" if prefs["enabled"] else "off (statusline on)"))
    print("emoji:   %s" % ("on" if prefs["emoji"] else "off"))
    print("compact: %s" % ("on" if prefs["compact"] else "off"))
    print("spacing: %s" % ("on" if prefs["spacing"] else "off"))
    print("bars:    %s" % prefs["bars"])
    print("hidden:  %s" % (", ".join(sorted(hidden_segments(prefs))) or "none"))
    print("prefs:   %s\n" % prefs_path())
    preview(False)


def cli(args):
    if not args or args[0] in ("status", "show-prefs"):
        show_status()
        return 0
    cmd, rest = args[0], args[1:]
    if cmd in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if cmd == "preview":
        preview(True)
        return 0
    prefs = load_prefs(apply_env=False)
    if cmd in TOGGLES:
        if rest and rest[0] not in ("on", "off"):
            print("usage: statusline %s [on|off]" % cmd, file=sys.stderr)
            return 2
        prefs[cmd] = (rest[0] == "on") if rest else not prefs[cmd]
        save_prefs(prefs)
        print("%s %s — applies on the next status line refresh" % (cmd, "on" if prefs[cmd] else "off"))
        return 0
    if cmd in ("on", "off") and not rest:
        prefs["enabled"] = cmd == "on"
        save_prefs(prefs)
        print("status line %s — applies on the next refresh" % cmd)
        return 0
    if cmd == "bars":
        if len(rest) != 1 or rest[0] not in BAR_STYLES:
            print("usage: statusline bars <%s>" % "|".join(BAR_STYLES), file=sys.stderr)
            return 2
        prefs["bars"] = rest[0]
        save_prefs(prefs)
        print("bars %s — applies on the next status line refresh" % rest[0])
        return 0
    if cmd in ("hide", "show"):
        unknown = [s for s in rest if s not in SEGMENTS]
        if not rest or unknown:
            print("usage: statusline %s <segment>...\nsegments: %s" % (cmd, " ".join(SEGMENTS)), file=sys.stderr)
            return 2
        hide = set(prefs["hide"]) | set(rest) if cmd == "hide" else set(prefs["hide"]) - set(rest)
        show = set(prefs["show"]) | set(rest) if cmd == "show" else set(prefs["show"]) - set(rest)
        prefs["hide"] = sorted(s for s in hide if s not in DEFAULT_HIDDEN)
        prefs["show"] = sorted(s for s in show if s in DEFAULT_HIDDEN)
        save_prefs(prefs)
        print("hidden: %s" % (", ".join(sorted(hidden_segments(prefs))) or "none"))
        return 0
    print("unknown command %r; see statusline --help" % cmd, file=sys.stderr)
    return 2


def run_cli(args):
    try:
        return cli(args)
    except OSError as exc:
        print("statusline: cannot write %s (%s)" % (exc.filename or prefs_path(), exc.strerror),
              file=sys.stderr)
        return 1


def main():
    if len(sys.argv) > 1 or sys.stdin.isatty():
        return run_cli(sys.argv[1:])
    raw = sys.stdin.read()
    # Claude Code always sends session JSON; empty input means a person ran
    # bare `statusline` without a tty (e.g. `! statusline` inside Claude Code).
    if not raw.strip():
        return run_cli([])
    try:
        render_stdin(raw)
    except Exception as exc:  # the bar must never go blank on a bug
        print("statusline: %s" % exc.__class__.__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

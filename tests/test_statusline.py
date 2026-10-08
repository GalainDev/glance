"""Tests for statusline.py. Run: python3 -m unittest discover -s <statusline>/tests"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "statusline.py")
sys.path.insert(0, os.path.dirname(HERE))
import statusline  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x07]*\x07")
SPACER = statusline.SPACER


def lines(text):
    """Visible rows, without colour codes or the blank spacer rows."""
    return [r for r in ANSI.sub("", text).split("\n") if r.strip().strip(SPACER)]


def full_input(cwd):
    now = time.time()
    return {
        "session_id": "abcdef12-3456-7890-abcd-ef1234567890",
        "session_name": "status line work",
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "workspace": {"current_dir": cwd},
        "cost": {"total_cost_usd": 2.5, "total_duration_ms": 3_900_000,
                 "total_lines_added": 12, "total_lines_removed": 3},
        "context_window": {"used_percentage": 42.4, "total_input_tokens": 84_321,
                           "context_window_size": 200_000},
        "effort": {"level": "high"},
        "thinking": {"enabled": True},
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": now + 2 * 3600 + 14 * 60 + 30},
            "seven_day": {"used_percentage": 91, "resets_at": now + 3 * 86400 + 4 * 3600 + 60},
        },
        "worktree": {"name": "feature-x"},
        "pr": {"number": 12, "url": "https://github.com/o/r/pull/12"},
    }


class Env:
    """Isolated HOME / XDG dirs so tests never touch the real prefs or cache."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.home = self.tmp.name
        self.project = os.path.join(self.home, "code", "app")
        os.makedirs(self.project)
        self.env = dict(os.environ, HOME=self.home,
                        XDG_CONFIG_HOME=os.path.join(self.home, "cfg"),
                        XDG_CACHE_HOME=os.path.join(self.home, "cache"))
        for key in ("STATUSLINE_EMOJI", "STATUSLINE_COMPACT", "STATUSLINE_SPACING",
                    "STATUSLINE_BARS", "NO_COLOR", "COLUMNS", "CLAUDE_CONFIG_DIR"):
            self.env.pop(key, None)

    def render(self, data, raw=None, **env):
        return subprocess.run([sys.executable, SCRIPT],
                              input=raw if raw is not None else json.dumps(data),
                              capture_output=True, text=True, timeout=10,
                              env=dict(self.env, **env))

    def cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True,
                              text=True, timeout=10, env=self.env, stdin=subprocess.DEVNULL)

    def account(self, **fields):
        with open(os.path.join(self.home, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": fields}, f)

    def prefs_file(self):
        return os.path.join(self.env["XDG_CONFIG_HOME"], "claude-statusline", "prefs.json")

    def close(self):
        self.tmp.cleanup()


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def rows(self, data=None, **env):
        proc = self.e.render(full_input(self.e.project) if data is None else data, **env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return lines(proc.stdout)

    def test_default_layout(self):
        rows = self.rows()
        self.assertEqual(len(rows), 3, rows)  # location, model, gauges
        self.assertTrue(rows[1].startswith("📁 ~/code/app"), rows[1])
        self.assertIn("🌿 none", rows[1])  # not a git repo
        self.assertIn("🌳 feature-x", rows[1])
        self.assertIn("PR #12", rows[1])
        self.assertNotIn("Opus", rows[1])
        self.assertTrue(rows[2].startswith("🧠 Opus 5.5"), rows[2])
        self.assertIn("⚡ high", rows[2])
        self.assertTrue(rows[2].endswith("│  sid: abcdef12"), rows[2])
        self.assertNotIn("think", rows[2])
        self.assertTrue(rows[0].startswith("ctx"), rows[0])
        self.assertIn("42%", rows[0])
        self.assertNotIn("84k", rows[0])
        self.assertIn("24%", rows[0])  # 23.5 rounds half-to-even → 24
        self.assertIn("↻2h14m", rows[0])
        self.assertIn("7d", rows[0])
        self.assertIn("↻3d4h", rows[0])

    def test_no_spacer_rows_by_default(self):
        out = self.e.render(full_input(self.e.project)).stdout
        self.assertNotIn(SPACER, out)
        self.assertEqual(len(out.rstrip("\n").split("\n")), 3)

    def test_spacing_on_adds_gap_rows_and_footer_gap(self):
        out = self.e.render(full_input(self.e.project), STATUSLINE_SPACING="1").stdout
        self.assertEqual(out.count("\n%s\n" % SPACER), 3)  # 2 between rows + 1 above footer
        self.assertTrue(out.endswith("\n%s\n" % SPACER), repr(out[-20:]))
        self.assertNotIn("\n \n", out)  # whitespace-only rows get dropped by Claude Code
        self.assertEqual(len(lines(out)), 3)

    def test_80_column_pane_keeps_weekly(self):
        rows = self.rows(COLUMNS="80")
        self.assertEqual(len(rows), 3, rows)
        self.assertIn("sid: abcdef12", rows[2])
        self.assertIn("7d", rows[0])
        self.assertIn("↻3d4h", rows[0])
        for row in rows:
            self.assertLessEqual(statusline.vwidth(row), 76, row)

    def test_narrow_pane_drops_low_priority_segments_first(self):
        rows = self.rows(COLUMNS="54")
        for row in rows:
            self.assertLessEqual(statusline.vwidth(row), 50, row)
        self.assertTrue(rows[1].startswith("📁 ~/code/app"))
        self.assertTrue(rows[2].startswith("🧠 Opus 5.5"))
        self.assertIn("ctx", rows[0])
        self.assertIn("5h", rows[0])     # weekly drops before the 5-hour gauge
        self.assertNotIn("7d", rows[0])

    def test_session_row_is_opt_in(self):
        self.e.cli("show", "name", "duration", "lines")
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertIn("status line work", rows[3])
        self.assertIn("1h05m", rows[3])
        self.assertIn("+12 −3", rows[3])
        self.assertNotIn("$", rows[3])  # cost still hidden
        self.assertNotIn("sid:", rows[3])  # sid lives on the model row

    def test_cache_segment(self):
        now = time.time()
        data = full_input(self.e.project)
        data["prompt_cache"] = {"caching_observed": True, "warm": True,
                                "expires_at": now + 52 * 60 + 20, "recache_tokens_if_cold": 274_865}
        out = self.e.render(data).stdout
        self.assertIn("cache \x1b[32m52m", out)
        self.assertIn("⚡ high  │  cache 52m  │  sid: abcdef12", lines(out)[2])
        data["prompt_cache"]["expires_at"] = now + 200
        self.assertIn("cache \x1b[33m3m", self.e.render(data).stdout)
        data["prompt_cache"].update(warm=False, expires_at=None)
        self.assertIn("cache \x1b[31mcold 275k", self.e.render(data).stdout)
        data["prompt_cache"]["caching_observed"] = False
        self.assertNotIn("cache", self.rows(data)[2])
        del data["prompt_cache"]
        self.assertNotIn("cache", self.rows(data)[2])

    def test_no_worktree_shows_none(self):
        data = full_input(self.e.project)
        del data["worktree"]
        self.assertIn("🌳 none", self.rows(data)[1])

    def test_colours_follow_thresholds(self):
        out = self.e.render(full_input(self.e.project)).stdout
        self.assertIn("\x1b[31m91%", out)   # 7d at 91 → red
        self.assertIn("\x1b[32m42%", out)   # ctx at 42 → green
        self.assertIn("\x1b]8;;https://github.com/o/r/pull/12\x07", out)

    def test_no_color(self):
        out = self.e.render(full_input(self.e.project), NO_COLOR="1").stdout
        self.assertNotIn("\x1b", out)

    def test_minimal_and_nulls_never_print_none(self):
        data = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": "/nonexistent/x"},
                "context_window": {"used_percentage": None}, "rate_limits": None,
                "effort": None, "cost": {"total_duration_ms": None}}
        out = "\n".join(self.rows(data))
        self.assertNotIn("None", out)
        self.assertIn("ctx —", out)
        self.assertNotIn("5h", out)

    def test_garbage_and_empty_stdin(self):
        for raw in ("not json", "[1,2]", '"str"', '{"model": 5, "workspace": []}'):
            proc = self.e.render(None, raw=raw)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(lines(proc.stdout), "blank for %r" % raw)
            self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    def test_bare_command_without_tty_shows_settings(self):
        # `! statusline` inside Claude Code: no args, no tty, empty stdin.
        for raw in ("", "\n", "  "):
            proc = self.e.render(None, raw=raw)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("emoji:   off", proc.stdout)
            self.assertIn("Preview from", proc.stdout)

    def test_no_rate_limits_shows_ctx_only(self):
        data = full_input(self.e.project)
        del data["rate_limits"]
        rows = self.rows(data)
        self.assertIn("ctx", rows[0])
        self.assertNotIn("7d", rows[0])

    def test_git_branch_and_dirty(self):
        repo = os.path.join(self.e.home, "repo")
        os.makedirs(repo)
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q", "-b", "trunk"], check=True, env=self.e.env)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True, env=self.e.env)
        clean = self.rows(full_input(repo))[1]
        self.assertIn("🌿 trunk", clean)
        self.assertNotIn("trunk*", clean)
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("x")
        # git info is cached for a few seconds; a new cache dir forces a re-read
        dirty = self.rows(full_input(repo), XDG_CACHE_HOME=os.path.join(self.e.home, "c2"))[1]
        self.assertIn("🌿 trunk*", dirty)

    def test_tier_from_account(self):
        self.e.account(organizationType="claude_max", userRateLimitTier="default_claude_max_20x")
        self.assertIn("Max 20x", self.rows()[2])
        self.e.account(organizationType="claude_pro", organizationRateLimitTier="default_claude_ai")
        self.assertIn("Pro", self.rows()[2])

    def test_tier_missing_or_odd_account(self):
        self.assertNotIn("Pro", self.rows()[2])  # no ~/.claude.json
        with open(os.path.join(self.e.home, ".claude.json"), "w") as f:
            f.write('{"oauthAccount": "weird"}')
        self.rows()

    def test_saves_last_input_for_preview(self):
        self.e.render(full_input(self.e.project))
        path = os.path.join(self.e.env["XDG_CACHE_HOME"], "claude-statusline", "last.json")
        with open(path) as f:
            self.assertEqual(json.load(f)["session_name"], "status line work")


class PrefsTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def rows(self, **env):
        proc = self.e.render(full_input(self.e.project), **env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return lines(proc.stdout)

    def test_emoji_toggle_applies_without_restart(self):
        rows = self.rows()
        self.assertIn("🧠 Opus 5.5", rows[2])  # folder/model/effort always carry emoji
        self.assertNotIn("⏳", rows[0])
        self.assertIn("emoji on", self.e.cli("emoji").stdout)
        rows = self.rows()
        self.assertIn("⏳ 5h", rows[0])
        self.assertIn("📊", rows[0])
        self.assertIn("emoji off", self.e.cli("emoji", "off").stdout)
        self.assertNotIn("⏳", "\n".join(self.rows()))

    def test_spacing_toggle(self):
        self.assertIn("spacing on", self.e.cli("spacing").stdout)
        self.assertEqual(self.e.render(full_input(self.e.project)).stdout.count(SPACER), 3)
        self.assertIn("spacing: on", self.e.cli("status").stdout)
        self.e.cli("spacing", "off")
        self.assertNotIn(SPACER, self.e.render(full_input(self.e.project)).stdout)

    def test_env_overrides_prefs(self):
        self.e.cli("emoji", "on")
        self.assertNotIn("⏳", "\n".join(self.rows(STATUSLINE_EMOJI="0")))
        self.assertIn("⏳", "\n".join(self.rows(STATUSLINE_EMOJI="1")))

    def test_compact_is_one_row(self):
        self.e.cli("compact", "on")
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("Opus 5.5", rows[0])
        self.assertIn("ctx 42%", rows[0])
        self.assertIn("7d 91%", rows[0])
        self.assertNotIn("sid:", rows[0])

    def test_hide_and_show(self):
        self.e.account(organizationType="claude_pro")
        self.assertIn("Pro", self.rows()[2])
        self.e.cli("hide", "tier", "sid")
        self.e.cli("show", "cost", "thinking")
        rows = self.rows()
        self.assertNotIn("Pro", rows[2])
        self.assertNotIn("sid:", rows[2])
        self.assertIn("thinking", rows[2])
        self.assertIn("~$2.50", rows[3])
        self.assertNotIn("status line work", rows[3])
        with open(self.e.prefs_file()) as f:
            saved = json.load(f)
        self.assertEqual(saved["hide"], ["sid", "tier"])
        self.assertEqual(saved["show"], ["cost", "thinking"])
        self.e.cli("show", "sid", "tier")
        self.e.cli("hide", "cost", "thinking")  # back to the defaults
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertIn("sid:", rows[2])

    def test_old_prefs_do_not_pin_old_defaults(self):
        os.makedirs(os.path.dirname(self.e.prefs_file()))
        with open(self.e.prefs_file(), "w") as f:
            json.dump({"emoji": False, "compact": False, "hide": ["cost"]}, f)
        rows = self.rows()
        self.assertEqual(len(rows), 3)  # session row stays hidden
        self.assertIn("sid:", rows[2])     # sid shown by the newer default

    def test_bar_styles(self):
        self.assertIn("█", self.rows()[0])  # block is the default
        self.assertIn("bars pill", self.e.cli("bars", "pill").stdout)
        row = self.rows()[0]
        self.assertIn("▰", row)
        self.assertNotIn("█", row)
        self.assertIn("●", self.rows(STATUSLINE_BARS="dots")[0])
        self.assertIn("bars:    pill", self.e.cli("status").stdout)

    def test_bad_cli_args(self):
        self.assertEqual(self.e.cli("bars", "chunky").returncode, 2)
        self.assertEqual(self.e.cli("bars").returncode, 2)
        self.assertEqual(self.e.cli("hide", "nope").returncode, 2)
        self.assertEqual(self.e.cli("emoji", "maybe").returncode, 2)
        self.assertEqual(self.e.cli("spacing", "half").returncode, 2)
        self.assertEqual(self.e.cli("frobnicate").returncode, 2)

    def test_unwritable_prefs_is_a_clean_error(self):
        blocker = os.path.join(self.e.home, "blocker")
        open(blocker, "w").close()  # a file where the config dir should go
        self.e.env["XDG_CONFIG_HOME"] = blocker
        proc = self.e.cli("emoji", "on")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot write", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_corrupt_prefs_fall_back_to_defaults(self):
        os.makedirs(os.path.dirname(self.e.prefs_file()))
        with open(self.e.prefs_file(), "w") as f:
            f.write("{oops")
        self.assertEqual(len(self.rows()), 3)

    def test_preview_and_status_without_history(self):
        out = self.e.cli("preview")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("sample input", out.stdout)
        for name in ("text", "emoji", "compact", "compact + emoji", "bars"):
            self.assertIn("── %s ──" % name, out.stdout)
        for style in ("block", "pill", "dots", "line"):
            self.assertRegex(out.stdout, r"\n%-6s ctx " % style)
        # bare `statusline` only shows status on a tty; without one it renders
        status = self.e.cli("status")
        self.assertIn("emoji:   off", status.stdout)


class FormatTests(unittest.TestCase):
    def test_countdown(self):
        self.assertEqual(statusline.countdown(-5), "now")
        self.assertEqual(statusline.countdown(30), "1m")
        self.assertEqual(statusline.countdown(42 * 60), "42m")
        self.assertEqual(statusline.countdown(2 * 3600 + 5 * 60), "2h05m")
        self.assertEqual(statusline.countdown(3 * 86400 + 4 * 3600), "3d4h")

    def test_display_path_and_width(self):
        home = "/Users/me"
        self.assertEqual(statusline.display_path("/Users/me/developer/app", home), "~/developer/app")
        self.assertEqual(statusline.display_path("/Users/me/developer/app/", home), "~/developer/app")
        self.assertEqual(statusline.display_path("/Users/me", home), "~")
        self.assertEqual(statusline.display_path("/Users/meow/x", home), "/Users/meow/x")
        self.assertEqual(statusline.display_path("/", home), "/")
        self.assertEqual(statusline.vwidth("\x1b[31mab\x1b[0m"), 2)
        self.assertEqual(statusline.vwidth("🧠x"), 3)

    def test_parse_git_status(self):
        info = statusline.parse_git_status(
            "# branch.oid abc\n# branch.head main\n# branch.ab +2 -1\n? new\n")
        self.assertEqual(info, {"branch": "main", "ahead": 2, "behind": 1, "dirty": True})
        self.assertIsNone(statusline.parse_git_status("# branch.head (detached)\n")["branch"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Log page colors: how a log line is split and classified. Pure logic from ajail-gui, no display needed."""
import ast, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    want_assign, want_def = {"LOG_LINE_RE", "LOG_KINDS"}, {"log_line_parts"}
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in want_assign)
            or (isinstance(n, ast.FunctionDef) and n.name in want_def)]
    ns = {"re": re}
    exec(compile(ast.Module(body, []), "ajail-gui", "exec"), ns)
    return ns["log_line_parts"]

parts = load()
TS = "[2026-11-11 20:39:24] "

class LogColor(unittest.TestCase):
    def test_split(self):
        self.assertEqual(parts(TS + "[app] START allow=[net] limits=[]\n"),
                         (TS, "[app] ", "START allow=[net] limits=[]", "info", "\n"))

    def test_app_output_has_no_timestamp(self):
        ts, name, body, kind, _ = parts("[app] thread 'main' panicked at x.rs:1:2:\n")
        self.assertEqual((ts, name, kind), ("", "[app] ", "err"))

    def test_exit(self):
        self.assertEqual(parts(TS + "[app] EXIT code=0\n")[3], "exit_ok")
        self.assertEqual(parts(TS + "[app] EXIT code=101\n")[3], "exit_err")

    def test_alsa_noise_is_dim_even_though_it_says_error(self):
        self.assertEqual(parts("[app] ALSA lib confmisc.c:855:(parse_card) [error.core] cannot find card '0'\n")[3], "dim")

    def test_warnings_and_errors(self):
        self.assertEqual(parts("[app] MESA-EGL: warning: failed to get driver name for fd -1\n")[3], "warn")
        self.assertEqual(parts("[app] MESA: error: ZINK: failed to choose pdev\n")[3], "err")
        self.assertEqual(parts(TS + "WARNING something odd\n")[3], "warn")

    def test_plain_line_and_no_trailing_newline(self):
        self.assertEqual(parts("[app] hello")[2:], ("hello", "", ""))

    def test_blank_and_weird_lines_do_not_crash(self):
        for l in ("\n", "", "[", "[]\n", TS + "\n"):
            parts(l)

if __name__ == "__main__":
    unittest.main()

#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Troubleshooting hints: which log lines suggest which setting. Pure logic from ajail-gui, no display needed."""
import ast, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    want_assign, want_def = {"DEFAULTS", "LOG_HINTS"}, {"effective_switches", "last_run", "log_hints"}
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in want_assign)
            or (isinstance(n, ast.FunctionDef) and n.name in want_def)]
    ns = {"re": re}
    exec(compile(ast.Module(body, []), "ajail-gui", "exec"), ns)
    return ns

G = load()
START = "[2026-11-11 20:00:00] [app] START allow=[net,sound,gpu] limits=[] seccomp=on dbus=on\n"
def exit_(code): return f"[2026-11-11 20:00:05] [app] EXIT code={code}\n"
def run(*body, code=1): return [START] + [f"[app] {b}\n" for b in body] + ([exit_(code)] if code is not None else [])
def cur(**kw): return {**G["DEFAULTS"], **kw}
def keys(lines, current=None): return [(k, w) for k, w, *_ in G["log_hints"](lines, current or cur())]

class Hints(unittest.TestCase):
    def test_sandbox_error_suggests_userns_when_it_is_off(self):
        l = run("FATAL: No usable sandbox! Update your kernel")
        self.assertEqual(keys(l, cur(userns=0)), [("userns", 1)])
        self.assertEqual(keys(l, cur(userns=1)), [])             # already on: nothing to suggest

    def test_read_only_filesystem(self):
        self.assertEqual(keys(run("open /app/save.dat: Read-only file system")), [("app_writable", 1)])

    def test_gpu_off_suggests_turning_it_on(self):
        self.assertEqual(keys(run("libEGL warning: failed to open /dev/dri/renderD128"), cur(gpu=0)), [("gpu", 1)])

    def test_render_only_suggestion_only_when_it_is_on(self):
        l = run("MESA: error: failed to open /dev/dri/card0")
        self.assertEqual(keys(l, cur(gpu=1, gpu_render_only=1)), [("gpu_render_only", 0)])
        self.assertEqual(keys(l, cur(gpu=1, gpu_render_only=0)), [])
        self.assertEqual(keys(l, cur(gpu=0, gpu_render_only=1)), [("gpu", 1)])      # render-only means nothing without gpu

    def test_x11_network_camera(self):
        self.assertEqual(keys(run("Error: cannot open display: :0")), [("x11", 1)])
        self.assertEqual(keys(run("curl: (6) Temporary failure in name resolution"), cur(net=0)), [("net", 1)])
        self.assertEqual(keys(run("cannot open /dev/video0"), cur(camera=0)), [("camera", 1)])

    def test_no_hint_for_a_run_that_ended_cleanly(self):
        self.assertEqual(keys(run("libEGL warning: something", code=0)), [])

    def test_no_hint_while_running_or_without_a_run(self):
        self.assertEqual(keys(run("No usable sandbox!", code=None), cur(userns=0)), [])
        self.assertEqual(keys([], cur(userns=0)), [])

    def test_only_the_last_run_counts(self):
        old = run("No usable sandbox!")
        new = run("all fine now", code=1)
        self.assertEqual(keys(old + new, cur(userns=0)), [])
        self.assertEqual(keys(new + old, cur(userns=0)), [("userns", 1)])

    def test_a_setting_is_suggested_once(self):
        l = run("libEGL warning: a", "MESA: error: b", "failed to open /dev/dri/x")
        self.assertEqual(keys(l, cur(gpu=0)), [("gpu", 1)])

    def test_unrelated_errors_give_nothing(self):
        self.assertEqual(keys(run("Segmentation fault (core dumped)", "assertion failed in game.c"), cur(userns=0, gpu=0, net=0)), [])

    def test_every_hint_names_a_real_switch_and_a_real_value(self):
        for pat, key, want, title, why, needs in G["LOG_HINTS"]:
            re.compile(pat)
            self.assertIn(key, G["DEFAULTS"]); self.assertIn(want, (0, 1)); self.assertTrue(title and why)
            for k, v in needs.items(): self.assertIn(k, G["DEFAULTS"])

    def test_effective_switches_layers(self):
        e = G["effective_switches"]({"s": {"net": 0, "x11": 1}}, {"s": {"x11": 0, "camera": 1}})
        self.assertEqual((e["net"], e["x11"], e["camera"], e["sound"]), (0, 0, 1, 1))
        self.assertEqual(G["effective_switches"]({"s": {}}, None)["net"], 1)

    def test_huge_log_is_handled(self):
        l = run(*(["x" * 5000] * 2000) + ["No usable sandbox!"])
        self.assertEqual(keys(l, cur(userns=0)), [("userns", 1)])

if __name__ == "__main__":
    unittest.main()

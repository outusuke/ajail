#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Troubleshooting hints: which log lines suggest which setting. Pure logic from ajail-gui, no display needed."""
import ast, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    want_assign, want_def = {"DEFAULTS", "LOG_HINTS", "HINT_LISTS"}, {"effective_switches", "effective_lists", "last_run", "log_hints"}
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
def keys(lines, current=None, lists=None): return [(k, w) for k, w, *_ in G["log_hints"](lines, current or cur(), lists)]

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

    def test_a_crash_with_no_reason_suggests_x11_as_a_guess_but_a_specific_message_wins(self):
        for l in (run("Aborted (core dumped)", code=None) + [exit_(134)], run(code=134)):
            self.assertEqual(keys(l), [("x11", 1)], l)
        self.assertEqual(keys(run(code=134), cur(x11=1)), [])                          # already on
        self.assertEqual(keys(run(code=1)), [])                                        # an ordinary failure is no guess
        l = run("Error: cannot open display: :0", code=134)
        self.assertEqual(len(G["log_hints"](l, cur())), 1)                             # the specific hint, not both
        self.assertNotIn("a guess", G["log_hints"](l, cur())[0][2])
        self.assertEqual(keys(run(code=134), cur()) and G["log_hints"](run(code=134), cur())[0][2][-9:], "(a guess)")

    WGPU = ["wgpu error: Validation Error", "Caused by:", "  In Device::create_shader_module, label = 'iced_wgpu.quad.solid.shader'",
            "Shader requires capability Capabilities(SHADER_FLOAT16_IN_FLOAT32)"]

    def test_a_wgpu_shader_failure_suggests_opengl_then_for_iced_the_processor(self):
        l = run(*self.WGPU, code=101)
        gl, cpu = ("env", "WGPU_BACKEND=gl"), ("env", "ICED_BACKEND=tiny-skia")
        self.assertEqual(keys(l, lists={}), [gl, cpu])                                       # OpenGL first, the processor second
        self.assertEqual(keys(l, lists={"env": ["WGPU_BACKEND=gl"]}), [cpu])                 # still failing with it: the next one
        self.assertEqual(keys(l, lists={"env": ["WGPU_BACKEND=gl", "ICED_BACKEND=tiny-skia"]}), [])
        self.assertEqual(keys(l, lists={"env": ["WGPU_BACKEND=vulkan", "ICED_BACKEND=wgpu"]}), [])     # chosen on purpose: leave them
        other = run("wgpu error: Validation Error", "Shader requires capability Capabilities(X)", code=101)    # wgpu, not iced
        self.assertEqual(keys(other, lists={}), [gl])
        plain = run("wgpu error: Validation Error", "something in another_crate", code=101)    # no capability message: nothing to say
        self.assertEqual(keys(plain, lists={}), [])
        self.assertEqual(keys(run(*self.WGPU, code=0), lists={}), [])                          # a clean run needs no advice

    def test_effective_lists_add_up_the_default_and_the_apps_own_file(self):
        g = {"env": ["A=1"], "allow": ["/x"]}
        self.assertEqual(G["effective_lists"](g, {"env": ["WGPU_BACKEND=gl"]}), {"env": ["A=1", "WGPU_BACKEND=gl"]})
        self.assertEqual(G["effective_lists"](g, None), {"env": ["A=1"]})
        self.assertEqual(keys(run(*self.WGPU, code=101), lists=G["effective_lists"]({"env": ["WGPU_BACKEND=gl"]}, None)), [("env", "ICED_BACKEND=tiny-skia")])

    def test_a_silent_crash_suggests_userns_only_while_it_is_off(self):
        for code in (132, 133, 134, 135, 136, 139, 159):
            l = run(code=code)
            self.assertIn(("userns", 1), keys(l, cur(userns=0)), code)
            self.assertNotIn(("userns", 1), keys(l, cur(userns=1)), code)                    # on already: that is not the reason
        l = run(code=139)
        self.assertEqual(keys(l, cur(userns=1)), [])                                         # a segfault with userns on is no guess of ours
        both = keys(run(code=134), cur(userns=0))
        self.assertEqual(both, [("userns", 1), ("x11", 1)])                                  # the sandbox guess first, X11 last
        self.assertEqual(keys(run(code=1), cur(userns=0)), [])                               # an ordinary failure is no guess
        self.assertEqual(keys(run("FATAL: No usable sandbox!", code=139), cur(userns=0)), [("userns", 1)])    # the specific one, once

    def test_the_exit_line_with_a_signal_name_still_counts(self):
        l = [START, "[2026-11-11 20:00:05] [app] EXIT code=139 (SIGSEGV)\n"]
        self.assertEqual(G["last_run"](l)[1], 139)
        self.assertIn(("userns", 1), keys(l, cur(userns=0)))

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
            if key in G["HINT_LISTS"]: self.assertRegex(want, r"^[A-Za-z_][A-Za-z0-9_]*=.+$")      # env=NAME=value
            else: self.assertIn(key, G["DEFAULTS"]); self.assertIn(want, (0, 1))
            self.assertTrue(title and why)
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

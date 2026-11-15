#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wayland security-context client (src/25_wayland.py) against a fake compositor, and what bwrap_args does with the
restricted socket it makes. No desktop needed."""
import os, select, socket, struct, tempfile, threading, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(name):
    src = open(f"{HERE}/{name}").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "under_test"}
    exec(compile(src[:-len("main()")], name, "exec"), ns)
    return ns

A = load("ajail")

class FakeCompositor(threading.Thread):
    """Just enough of a Wayland compositor to test the security-context client: registry, sync, the manager."""
    def __init__(self, path, wl_string, with_manager=True, fail_on_commit=False):
        super().__init__(daemon=True)
        self.wl_string, self.with_manager, self.fail_on_commit = wl_string, with_manager, fail_on_commit
        self.engine = self.app_id = self.instance = self.bound = self.listen = self.close_fd = None
        self.committed, self.running = False, True
        self.srv = socket.socket(socket.AF_UNIX); self.srv.bind(path); self.srv.listen(4); self.srv.settimeout(0.1)
        self.start()

    def stop(self):
        self.running = False
        self.join(2)
        self.srv.close()
        if self.listen: self.listen.close(); self.listen = None
        if self.close_fd is not None:
            try: os.close(self.close_fd)
            except OSError: pass
            self.close_fd = None

    def run(self):
        while self.running:
            try: conn, _ = self.srv.accept()
            except (socket.timeout, OSError): continue
            conn.settimeout(2)
            try: self.serve(conn)
            except (OSError, struct.error): pass
            finally: conn.close()

    def take(self, conn, n):
        data = b""
        while len(data) < n:
            chunk, anc, _, _ = conn.recvmsg(n - len(data), socket.CMSG_SPACE(8))
            if not chunk: raise OSError("closed")
            data += chunk
            for _, _, raw in anc:
                self.fds = getattr(self, "fds", []) + list(struct.unpack(f"={len(raw) // 4}i", raw[:len(raw) // 4 * 4]))
        return data

    def event(self, conn, obj, op, payload=b""):
        conn.sendall(struct.pack("=II", obj, ((8 + len(payload)) << 16) | op) + payload)

    def string(self, data, off=0):
        (n,) = struct.unpack_from("=I", data, off)
        return data[off + 4:off + 4 + n - 1].decode(), off + 4 + (n + 3) // 4 * 4

    def serve(self, conn):
        registry = manager = context = None
        while self.running:
            obj, word = struct.unpack("=II", self.take(conn, 8))
            op = word & 0xFFFF
            data = self.take(conn, (word >> 16) - 8)
            if obj == 1 and op == 1:                                                          # get_registry
                (registry,) = struct.unpack("=I", data)
                self.event(conn, registry, 0, struct.pack("=I", 1) + self.wl_string("wl_compositor") + struct.pack("=I", 5))
                if self.with_manager:
                    self.event(conn, registry, 0, struct.pack("=I", 7) + self.wl_string("wp_security_context_manager_v1") + struct.pack("=I", 1))
            elif obj == 1 and op == 0:                                                        # sync
                (cb,) = struct.unpack("=I", data)
                self.event(conn, cb, 0, struct.pack("=I", 0))
            elif obj == registry and op == 0:                                                 # bind
                off = 4
                iface, off = self.string(data, off)
                ver, manager = struct.unpack_from("=II", data, off)
                self.bound = (iface, ver)
            elif obj == manager and op == 1:                                                  # create_listener
                (context,) = struct.unpack("=I", data)
                self.listen = socket.socket(fileno=self.fds[0]); self.close_fd = self.fds[1]
            elif obj == context and op in (1, 2, 3):
                value, _ = self.string(data)
                setattr(self, {1: "engine", 2: "app_id", 3: "instance"}[op], value)
            elif obj == context and op == 4:                                                  # commit
                self.committed = True
                if self.fail_on_commit:
                    msg = b"invalid metadata\0"
                    self.event(conn, 1, 0, struct.pack("=III", context, 3, len(msg)) + msg + b"\0" * (-len(msg) % 4))


# ---------------------------------------------------------------- real isolation


class Client(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.warned = []
        p = unittest.mock.patch.dict(A, {"warn": lambda m, n=None: self.warned.append(m)})
        p.start(); self.addCleanup(p.stop)

    def test_client_against_a_fake_compositor(self):
        sock = f"{self.tmp}/wl"
        comp = FakeCompositor(sock, A["wl_string"]); self.addCleanup(comp.stop)
        self.assertTrue(A["wl_supported"](sock))
        ctx = A["wayland_context"](sock, f"{self.tmp}/run/app.wl", "firefox", "firefox-42")
        self.assertIsNotNone(ctx, self.warned)
        self.assertEqual((comp.engine, comp.app_id, comp.instance, comp.committed), ("org.ajail", "firefox", "firefox-42", True))
        self.assertEqual(comp.bound, ("wp_security_context_manager_v1", 1))
        self.assertEqual(ctx.path, f"{self.tmp}/run/app.wl")
        listen = comp.listen
        self.assertEqual(listen.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)       # a listening socket...
        c = socket.socket(socket.AF_UNIX); c.connect(f"{self.tmp}/run/app.wl"); c.close()      # ...at the path the app will use
        listen.settimeout(2); listen.accept()[0].close()
        self.assertEqual(oct(os.stat(f"{self.tmp}/run").st_mode & 0o777), "0o700")
        poll = select.poll(); poll.register(comp.close_fd, select.POLLHUP)
        self.assertEqual(poll.poll(100), [])                                                    # the compositor keeps listening...
        ctx.close()
        self.assertTrue(poll.poll(2000), "closing the context must make the close fd hang up")  # ...until ajail lets go
        self.assertFalse(os.path.exists(f"{self.tmp}/run/app.wl"))

    def test_client_copes_with_no_support_and_with_errors(self):
        sock = f"{self.tmp}/wl"
        comp = FakeCompositor(sock, A["wl_string"], with_manager=False); self.addCleanup(comp.stop)
        self.assertFalse(A["wl_supported"](sock))
        self.assertIsNone(A["wayland_context"](sock, f"{self.tmp}/run/x.wl", "a", "b"))
        self.assertEqual(self.warned, [])                                                        # nothing to warn about, just not there
        comp.stop()
        sock2 = f"{self.tmp}/wl2"
        bad = FakeCompositor(sock2, A["wl_string"], fail_on_commit=True); self.addCleanup(bad.stop)
        self.assertIsNone(A["wayland_context"](sock2, f"{self.tmp}/run/y.wl", "a", "b"))
        self.assertTrue(any("invalid metadata" in w for w in self.warned), self.warned)
        self.assertFalse(os.path.exists(f"{self.tmp}/run/y.wl"), "a failed attempt leaves no socket behind")
        self.assertFalse(A["wl_supported"](f"{self.tmp}/nothing-here"))
        self.assertIsNone(A["wayland_context"](f"{self.tmp}/nothing-here", f"{self.tmp}/run/z.wl", "a", "b"))

    def test_request_numbers_match_the_protocol(self):
        import re
        xml = "/usr/share/wayland-protocols/staging/security-context/security-context-v1.xml"
        if not os.path.exists(xml): self.skipTest("wayland-protocols is not installed")
        text = open(xml).read()
        reqs = {m[1]: re.findall(r'<request name="(\w+)"', m[2]) for m in re.finditer(r'<interface name="(\w+)"[^>]*>(.*?)</interface>', text, re.S)}
        self.assertEqual(reqs["wp_security_context_manager_v1"], ["destroy", "create_listener"])
        self.assertEqual(reqs["wp_security_context_v1"], ["destroy", "set_sandbox_engine", "set_app_id", "set_instance_id", "commit"])


class Sandbox(unittest.TestCase):
    """What bwrap_args does with the restricted socket."""
    WP, WL = "/run/user/1000/wayland-0", "/run/user/1000/ajail/7.wl"

    def build(self, wl=None, **kw):
        c = {**A["DEFAULTS"], **A["NUM"], "allow": [], "allow_ro": [], "env": [], "talk": [], "portal": [], "xdg": []}
        c.update(kw)
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000", "PATH": "/usr/bin"}
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
             unittest.mock.patch.dict(A["bwrap_args"].__globals__, {"bwrap_has": lambda o: True, "wayland_path": lambda: self.WP}), \
             unittest.mock.patch.object(A["os"].path, "exists", lambda p: True):
            a = A["bwrap_args"]("t", "/fake-root", c, ["/app/AppRun"], None, None, (), None, None, wl)
        triples = [a[i:i + 3] for i in range(len(a) - 2)]
        env_out = {a[i + 1]: a[i + 2] for i in range(len(a) - 2) if a[i] == "--setenv"}
        return a, triples, env_out

    def test_the_app_gets_the_restricted_socket_in_place_of_the_real_one(self):
        _, t, env = self.build(self.WL)
        self.assertIn(["--ro-bind", self.WL, self.WP], t)
        self.assertNotIn(["--ro-bind", self.WP, self.WP], t)
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")

    def test_without_one_the_real_socket_is_bound(self):
        _, t, _ = self.build(None)
        self.assertIn(["--ro-bind", self.WP, self.WP], t)

    def test_strict_means_no_wayland_without_a_restricted_socket(self):
        a, t, env = self.build(None, wayland_strict=1)
        self.assertNotIn(self.WP, a); self.assertNotIn("WAYLAND_DISPLAY", env)
        self.assertEqual(env["ELECTRON_OZONE_PLATFORM_HINT"], "x11")
        _, t, env = self.build(self.WL, wayland_strict=1)                                         # with one, strict changes nothing
        self.assertIn(["--ro-bind", self.WL, self.WP], t); self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")

    def test_wayland_off_wins_over_a_restricted_socket(self):
        a, _, env = self.build(self.WL, wayland=0)
        self.assertNotIn(self.WL, a); self.assertNotIn(self.WP, a); self.assertNotIn("WAYLAND_DISPLAY", env)

    def test_the_restricted_socket_is_never_writable(self):
        a, _, _ = self.build(self.WL)
        for i, x in enumerate(a):
            if x in (self.WL, self.WP) and i > 0: self.assertIn(a[i - 1], ("--ro-bind", self.WL), a[i - 3:i + 1])
        self.assertNotIn("--bind", [a[i - 1] for i, x in enumerate(a) if x == self.WL])


if __name__ == "__main__":
    unittest.main()

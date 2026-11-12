#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Icon lookup for installed apps: pure logic from ajail, no sandbox needed."""
import ast, glob, os, re, struct, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    tree = ast.parse(open(f"{HERE}/ajail").read())
    want_assign, want_def = {"ICON_SIZES", "SVG_HEAD"}, {"inside", "icon_size_dir", "find_icon"}
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in want_assign)
            or (isinstance(n, ast.FunctionDef) and n.name in want_def)]
    ns = {"re": re, "os": os, "glob": glob, "struct": struct}
    exec(compile(ast.Module(body, []), "ajail", "exec"), ns)
    return ns

G = load()

def png(w, h=None):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h or w) + b"\x08\x06\0\0\0" + b"\0" * 64

class Icons(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.root = self._t.name

    def put(self, name, data):
        os.makedirs(os.path.dirname(f"{self.root}/{name}"), exist_ok=True)
        open(f"{self.root}/{name}", "wb").write(data)

    def test_sizes_the_theme_does_not_list_go_in_the_nearest_listed_folder(self):
        d = G["icon_size_dir"]
        self.assertEqual([d(w) for w in (16, 256, 512)], ["16x16", "256x256", "512x512"])
        self.assertEqual([d(w) for w in (100, 300, 1024, 4096)], ["96x96", "256x256", "512x512", "512x512"])

    def test_a_1024_pixel_png_is_found_and_filed_under_512(self):
        self.put(".DirIcon", png(1024)); self.assertEqual(G["find_icon"](self.root, None)[1:], ("png", "512x512"))

    def test_a_300_pixel_png_named_by_the_desktop_file_is_filed_under_256(self):
        self.put("app.png", png(300)); self.assertEqual(G["find_icon"](self.root, "app")[1:], ("png", "256x256"))

    def test_an_svg_with_a_long_prolog_is_found(self):
        self.put(".DirIcon", b'<?xml version="1.0"?>\n<!-- ' + b"x" * 3000 + b' -->\n<SVG xmlns="http://www.w3.org/2000/svg"/>')
        self.assertEqual(G["find_icon"](self.root, None)[1:], ("svg", "scalable"))

    def test_not_an_image_gives_none(self):
        self.put(".DirIcon", b"hello"); self.assertIsNone(G["find_icon"](self.root, None))

if __name__ == "__main__":
    unittest.main()

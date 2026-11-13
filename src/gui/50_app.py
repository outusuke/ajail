# ======== 50_app: Application object and startup arguments.

class App(Adw.Application):
    def __init__(self, log=None, install=None):
        kw = {"flags": Gio.ApplicationFlags.NON_UNIQUE} if (log is not None or install) else {}
        super().__init__(application_id="org.ajail.Gui", **kw)
        self.log_scope = log
        self.install_path = install

    def do_activate(self):
        first = self.props.active_window is None
        win = self.props.active_window or Win(self)
        win.present()
        if first and self.install_path:
            win.offer_install(self.install_path, quit_on_close=True)
        elif first and self.log_scope is not None:
            win.open_trouble(self.log_scope or None)


for flag, on in (("--install-menu", True), ("--remove-menu", False)):
    if flag in sys.argv[1:]:                 # no window: what `ajail desktop --install-menu` runs
        try:
            set_menu_entry(on)
        except OSError as e:
            sys.exit(f"ajail-gui: {e.strerror or e}")
        print(f"wrote {MENU_DESKTOP}" if on else "removed AppImage Jail from the app menu")
        sys.exit(0)
argv, log = sys.argv[:], None
if "--log" in argv:                      # ajail-gui --log [NAME]  opens the log page
    i = argv.index("--log")
    nxt = argv[i + 1] if i + 1 < len(argv) else ""
    log = nxt if NAME_RE.match(nxt) else ""
    del argv[i:i + (2 if log else 1)]
inst = None
if "--install" in argv:                  # ajail-gui --install FILE  offers Install or Run once
    i = argv.index("--install")
    if i + 1 < len(argv) and os.path.isfile(argv[i + 1]):
        inst = os.path.realpath(argv[i + 1])
    del argv[i:i + 2]
sys.exit(App(log, inst).run(argv))

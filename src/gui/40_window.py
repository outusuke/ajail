# ======== 40_window: The GTK window (Win): every screen of the app.

def esc(s):
    return GLib.markup_escape_text(s)

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="AppImage Jail", default_width=560, default_height=800)
        self._busy = False
        self.rows, self.badges = [], {}
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)
        self.nav.add(self.build_main())
        GLib.timeout_add_seconds(3, self.tick)

    def toast(self, msg):
        # Messages can contain text from an app's stderr, so never parse them as markup.
        self.toasts.add_toast(Adw.Toast(title=msg, use_markup=False))

    def confirm(self, heading, body, label, cb):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", label)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda d, r: cb() if r == "ok" else None)
        dlg.present(self)

    def ask(self, heading, labels, cb):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ents = []
        for l in labels:
            e = Gtk.Entry(placeholder_text=l, activates_default=True)
            box.append(e)
            ents.append(e)
        dlg = Adw.AlertDialog(heading=heading)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Add")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda d, r: cb([e.get_text().strip() for e in ents]) if r == "ok" else None)
        dlg.present(self)

    def build_main(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup()
        self.force = Adw.SwitchRow(
            title="Sandbox every AppImage",
            subtitle="Applies however an app is started. Asks for your password when changed.")
        self.force.set_active(os.path.exists(RULE))
        self.force.connect("notify::active", self.on_force)
        g.add(self.force)
        self.ask_row = Adw.SwitchRow(      # not "ask": that name is the dialog helper method
            title="Ask before opening a new AppImage",
            subtitle="Default for all apps. Offers Install or Run once when you double-click one. Each app can "
                     "override it on its own page. Needs Sandbox every AppImage.")
        self.ask_row.set_active(get_open_mode() == "ask")
        self.ask_handler = self.ask_row.connect(
            "notify::active", lambda r, _p: set_open_mode("ask" if r.get_active() else "run"))
        g.add(self.ask_row)
        can_mount = bool(shutil.which("squashfuse") and shutil.which("fusermount3"))
        ig = Adw.PreferencesGroup(title="Installing")
        self.mount_row = Adw.SwitchRow(
            title="Use less disk space",
            subtitle="Default for all apps. Keeps installed apps compressed and mounts them at launch. Slower, and the "
                     "file is read outside the sandbox, so only for apps you trust. Experimental. Applies to Install, "
                     "never to Run once. Each app can override it on its own page."
                     + ("" if can_mount else " Needs squashfuse and fuse3."),
            sensitive=can_mount)
        self.mount_row.set_active(can_mount and ui_read().get("mount") == "1")
        self.mount_handler = self.mount_row.connect(
            "notify::active", lambda r, _p: (ui_set("mount", "1" if r.get_active() else "0"), self.fill_original()))
        ig.add(self.mount_row)
        self.orig_row = Adw.ComboRow(title="After installing, the AppImage file")
        self.orig_state = {"keys": []}
        self.orig_handler = self.orig_row.connect("notify::selected", self.on_original)
        ig.add(self.orig_row)
        self.fill_original()
        self.menu = Adw.SwitchRow(
            title="Show AppImage Jail in the app menu",
            subtitle="Adds a launcher for this window to your application menu.")
        self.menu.set_active(menu_entry_exists())

        def on_menu(r, _p):
            try:
                set_menu_entry(r.get_active())
                self.toast("Added to the app menu" if r.get_active() else "Removed from the app menu")
            except OSError as e:
                self.toast(f"Could not change it: {e.strerror or e}")

        self.menu.connect("notify::active", on_menu)
        g.add(self.menu)
        page.add(g)
        page.add(ig)

        self.apps_group = Adw.PreferencesGroup(title="Apps")
        page.add(self.apps_group)

        rg = Adw.PreferencesGroup()
        rbtn = Gtk.Button(label="Reset all settings…", halign=Gtk.Align.CENTER)
        rbtn.add_css_class("destructive-action")

        def do_reset_all():
            try:
                write_cfg(None, empty())                    # default permissions
                if os.path.exists(UICONF):
                    os.unlink(UICONF)                       # ask switch + every app's remembered choices
                for fn in os.listdir(APPS) if os.path.isdir(APPS) else []:
                    if fn.endswith(".conf"):                # every app's own permission toggles
                        write_cfg(fn[:-5], empty())         # empty, not deleted: the file keeps the app listed
            except OSError as e:
                self.toast(f"Could not reset: {e.strerror or e}")
                return
            self.ask_row.handler_block(self.ask_handler)
            self.ask_row.set_active(True)                   # the built-in default
            self.ask_row.handler_unblock(self.ask_handler)
            self.mount_row.handler_block(self.mount_handler)
            self.mount_row.set_active(False)                # the built-in default
            self.mount_row.handler_unblock(self.mount_handler)
            self.fill_original()
            self.fill_apps()
            self.toast("Settings reset to the defaults")

        rbtn.connect("clicked", lambda *_: self.confirm(
            "Reset all settings?",
            "The default permissions, every app's own permissions and every remembered open and disk-space "
            "choice go back to the built-in settings. The app menu entry, Sandbox every AppImage and each "
            "app's data are not touched.", "Reset", do_reset_all))
        rg.add(rbtn)
        page.add(rg)

        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add an AppImage")
        add.connect("clicked", self.on_add)
        header.pack_start(add)
        ts = Gtk.Button(icon_name="utilities-terminal-symbolic", tooltip_text="Troubleshooting")
        ts.connect("clicked", lambda *_: self.open_trouble(None))
        header.pack_end(ts)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.main = Adw.NavigationPage(title="AppImage Jail", child=view)
        self.fill_apps()
        return self.main

    def fill_apps(self):
        for r in self.rows:
            self.apps_group.remove(r)
        self.rows, self.badges = [], {}
        d = dump("--all")
        G, apps = d["global"], d.get("apps", {})
        names = app_names()
        self.apps_group.set_description(
            "Select an app to change what it can access." if names else
            "No apps yet. Press + to add an AppImage, or just launch one.")
        for scope, title in [(None, "All apps (default)")] + [(n, n) for n in names]:
            A = None if scope is None else apps.get(scope, empty())
            row = Adw.ActionRow(title=esc(title), subtitle=esc(summary(G, A)), activatable=True)
            if scope:
                b = Gtk.Label(label="Running", visible=False)
                b.add_css_class("success")
                b.add_css_class("caption")
                row.add_suffix(b)
                self.badges[scope] = b
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda r, s=scope: self.open_app(s))
            self.apps_group.add(row)
            self.rows.append(row)
        self.tick()

    def tick(self):
        on = os.path.exists(RULE)
        if self.force.get_sensitive() and self.force.get_active() != on:
            self._busy = True
            self.force.set_active(on)
            self._busy = False
        names = running_pids()
        for n, b in self.badges.items():
            b.set_visible(n in names)
        return True

    def on_force(self, row, _):
        if self._busy:
            return
        want = row.get_active()
        if want == os.path.exists(RULE):
            return
        row.set_sensitive(False)

        def done(r):
            self._busy = True
            row.set_active(os.path.exists(RULE))
            self._busy = False
            row.set_sensitive(True)
            if r.returncode:
                msg = (r.stderr or r.stdout or "Cancelled").strip().splitlines()
                self.toast((msg[-1] if msg else "Cancelled")[:120])
            return False

        def work():
            r = run([SELF, "on" if want else "off"])
            GLib.idle_add(done, r)

        threading.Thread(target=work, daemon=True).start()

    def on_add(self, *_):
        dlg = Gtk.FileDialog(title="Choose an AppImage")
        flt = Gtk.FileFilter()
        flt.set_name("AppImage")
        flt.add_pattern("*.AppImage")
        flt.add_pattern("*.appimage")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def cb(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self.offer_install(f.get_path())

        dlg.open(self, None, cb)

    def fill_original(self, *_):
        """(Re)build the 'after installing' dropdown. 'Move into app folder' exists only while 'Use less disk space' is on."""
        keys = original_choices(self.mount_row.get_active())
        want = get_install_original()
        if want not in keys:
            want = "ask"               # a remembered "move" is not offered (or used) while the switch is off
        self.orig_row.handler_block(self.orig_handler)
        self.orig_state["keys"] = keys
        self.orig_row.set_model(Gtk.StringList.new([ORIGINAL[k] for k in keys]))
        self.orig_row.set_selected(keys.index(want))
        self.orig_row.handler_unblock(self.orig_handler)
        self.orig_row.set_subtitle(original_hint(want))

    def on_original(self, row, _p):
        keys = self.orig_state["keys"]
        i = row.get_selected()
        if 0 <= i < len(keys):
            ui_set("install_original", keys[i])
            row.set_subtitle(original_hint(keys[i]))

    def offer_install(self, path, quit_on_close=False, then=None):
        name = run([SELF, "name", path]).stdout.strip()
        if not NAME_RE.match(name):
            self.toast("Could not read that file. Is ajail installed?")
            return
        d = dump(name)
        update = os.path.isdir(f"{INSTALLED}/{name}/root")
        dlg = Adw.AlertDialog(
            heading=f"{'Update' if update else 'Install'} {name}?",
            body=f"It runs sandboxed and is added to your app menu. Allowed: {summary(d['global'], d.get('app'))}.")
        remember = None
        if quit_on_close:                    # only when opened by double-clicking an AppImage
            remember = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            row = Adw.ActionRow(title=f"Don't ask again for {name}",
                                subtitle="Double-clicking this AppImage will run it straight away. "
                                         "You can change that on its page in AppImage Jail.")
            row.add_prefix(remember)
            row.set_activatable_widget(remember)
            opts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            opts.add_css_class("boxed-list")
            opts.append(row)
            dlg.set_extra_child(opts)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("once", "Run once")
        dlg.add_response("install", "Update" if update else "Install")
        dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("install")
        dlg.set_close_response("cancel")

        def done(_d, r):
            if r == "install":
                # What happens to the file, and whether the app is kept compressed, come from the settings.
                mount = bool(shutil.which("squashfuse") and shutil.which("fusermount3")) and app_mount(name)
                self.install_file(path, None, then or (lambda: self.open_app(name)), mount)
                return
            if r == "once":
                if remember is not None and remember.get_active():
                    ui_set(f"open_mode.{name}", "run")
                # Run once always unpacks inside the sandbox: it never mounts the image.
                subprocess.Popen([SELF, "run-file", "--once", path], start_new_session=True)
            if quit_on_close:
                self.get_application().quit()

        dlg.connect("response", done)
        self.dialog = dlg
        dlg.present(self)

    def install_file(self, path, original=None, then=None, mount=False):
        """original is "ask", "trash", "keep" or "move" (move only with mount); None means the saved setting."""
        original = original or get_install_original()
        if original == "move" and not mount:
            original = "ask"
        self.toast("Installing…")
        flags = (["--mount"] if mount else []) + (["--trash"] if original == "trash" else
                                                  ["--move"] if original == "move" else [])

        def work():
            r = run([SELF, "install", path] + flags)
            GLib.idle_add(done, r)

        def done(r):
            if r.returncode:
                self.toast(((r.stderr or r.stdout).strip().splitlines() or ["Install failed"])[-1][:120])
            else:
                if "--move" in flags and os.path.exists(path):
                    self.toast("Installed. The file couldn't be moved (removable or network drive?), so it was copied "
                               "and the original kept")
                elif "--move" in flags:
                    self.toast("Installed. The AppImage was moved into AppImage Jail")
                else:
                    self.toast("Installed")
                self.fill_apps()
                if then:
                    then()
                if original == "ask" and os.path.isfile(path):
                    self.ask_trash(path)
            return False

        threading.Thread(target=work, daemon=True).start()

    def ask_trash(self, path):
        """After a successful install: offer to trash the AppImage file. Asked afterwards, so it can be undone from the Trash."""
        dlg = Adw.AlertDialog(
            heading="Move the AppImage to the Trash?",
            body=f"{os.path.basename(path)} is installed and AppImage Jail keeps its own copy, so you no longer need "
                 "this file. You can restore it from the Trash later. To skip this question, choose an answer under "
                 "Installing on the main page.")
        dlg.add_response("keep", "Keep file")
        dlg.add_response("trash", "Move to Trash")
        dlg.set_response_appearance("trash", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("trash")
        dlg.set_close_response("keep")

        def done(_d, r):
            if r != "trash":
                return
            res = run(["gio", "trash", path], timeout=120)
            self.toast("AppImage moved to the Trash" if res.returncode == 0 else "Could not move the AppImage to the Trash")

        dlg.connect("response", done)
        self.dialog = dlg
        dlg.present(self)

    def uninstall(self, scope):
        origin = ""      # set when the install moved the AppImage into AppImage Jail: uninstalling would delete it
        if os.path.isfile(f"{INSTALLED}/{scope}/origin") and os.path.isfile(f"{INSTALLED}/{scope}/image"):
            try:
                origin = open(f"{INSTALLED}/{scope}/origin").read().strip()
            except OSError:
                pass
        body = "It is removed from your app menu. Keep its saved data in case you reinstall it?"
        if origin:
            body = (f"The AppImage was moved into AppImage Jail, so it is put back at {origin} first. "
                    "Keep its saved data in case you reinstall it?")
        dlg = Adw.AlertDialog(heading=f"Uninstall {scope}?", body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("keep", "Keep data")
        dlg.add_response("purge", "Move data to Trash too")
        dlg.set_response_appearance("purge", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def last_line(res, fallback):
            return ((res.stderr or res.stdout).strip().splitlines() or [fallback])[-1][:120]

        def finish(res, ok_msg):
            if res.returncode:
                self.toast(last_line(res, "Uninstall failed"))
                return
            self.nav.pop()
            self.fill_apps()
            self.toast(ok_msg)

        def plain(purge, discard=False):
            flags = (["--purge"] if purge else []) + (["--discard-image"] if discard else [])
            finish(run([SELF, "uninstall", scope] + flags), "Uninstalled")

        def done(_d, r):
            if r == "cancel":
                return
            purge = r == "purge"
            if not origin:
                plain(purge)
                return
            res = run([SELF, "restore", scope])      # puts the file back and uninstalls (data is kept)
            if res.returncode:
                # Never leave the person stuck: say why, and let them choose to uninstall and delete the stored file.
                fb = Adw.AlertDialog(heading="Couldn't put the AppImage back",
                                     body=last_line(res, "Unknown error") + "\n\nYou can put it in another folder, or "
                                     "uninstall anyway (the AppImage stored in AppImage Jail is deleted).")
                fb.add_response("cancel", "Cancel")
                fb.add_response("folder", "Choose another folder…")
                fb.add_response("discard", "Uninstall anyway")
                fb.set_response_appearance("folder", Adw.ResponseAppearance.SUGGESTED)
                fb.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
                fb.set_default_response("folder")
                fb.set_close_response("cancel")

                def fb_done(_d, r2):
                    if r2 == "discard":
                        plain(purge, True)
                    elif r2 == "folder":
                        fd = Gtk.FileDialog(title="Choose where to put the AppImage")

                        def picked(d2, res2):
                            try:
                                folder = d2.select_folder_finish(res2).get_path()
                            except GLib.Error:
                                return
                            r3 = run([SELF, "restore", scope, "--to", folder])      # puts the file back and uninstalls
                            if r3.returncode:
                                self.toast(last_line(r3, "Could not put the AppImage back"))
                                return
                            if purge:
                                r3 = run([SELF, "uninstall", scope, "--purge"])
                            finish(r3, f"Uninstalled. The AppImage was put in {folder}")

                        fd.select_folder(self, None, picked)

                fb.connect("response", fb_done)
                fb.present(self)
                return
            if purge:
                res = run([SELF, "uninstall", scope, "--purge"])
            finish(res, f"Uninstalled. The AppImage was put back at {origin}")

        dlg.connect("response", done)
        self.dialog = dlg
        dlg.present(self)

    def open_app(self, scope):
        d = dump(scope or "")
        G = d["global"]
        A = d.get("app") or empty()
        R = G if scope is None else A      # the settings being edited
        title = scope or "All apps (default)"
        page = Adw.PreferencesPage()
        marks, syncing = [], [False]

        tech = Adw.ActionRow(title="Command", subtitle_selectable=True)
        tech.add_css_class("monospace")

        def refresh_tech():
            r = run([SELF, "preview"] + ([scope] if scope else []))
            tech.set_subtitle(esc((r.stdout or r.stderr).strip()))

        def commit():
            try:
                write_cfg(scope, R)
            except OSError as e:
                self.toast(f"Could not save: {e.strerror or e}")
            for m in marks:
                m()
            GLib.idle_add(refresh_tech)

        def eff(k):
            v = dict(DEFAULTS)
            v.update(G["s"])
            if scope:
                v.update(A["s"])
            return bool(v[k])

        def default_for(k):
            v = dict(DEFAULTS)
            if scope:
                v.update(G["s"])
            return v[k]

        if scope:
            ag = Adw.PreferencesGroup(title="App")
            try:
                src = open(f"{SOURCES}/{scope}").read().strip()
            except OSError:
                src = ""
            kind = ("root" if os.path.isdir(f"{INSTALLED}/{scope}/root")
                    else "mount" if os.path.isfile(f"{INSTALLED}/{scope}/image") else None)
            inst = bool(kind)
            if inst:
                arow = Adw.ActionRow(title="Installed", subtitle="In your app menu. The AppImage file is no longer needed."
                                     if kind == "root" else
                                     "In your app menu. Kept compressed (experimental) and mounted when the app starts.")
                launch = Gtk.Button(label="Launch", valign=Gtk.Align.CENTER)
                launch.connect("clicked", lambda *_: subprocess.Popen([SELF, "run", scope], start_new_session=True))
            else:
                arow = Adw.ActionRow(title="AppImage file", subtitle=esc(src or "Unknown until the first launch"))
                launch = Gtk.Button(label="Launch", valign=Gtk.Align.CENTER, sensitive=bool(src and os.path.isfile(src)))
                launch.connect("clicked", lambda *_: subprocess.Popen(      # run once never mounts
                    [SELF, "run-file", "--once", src], start_new_session=True))
            stop = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER)

            def do_stop(*_):
                for pid in running_pids(scope):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass
                self.toast("Stopping…")

            stop.connect("clicked", do_stop)
            logb = Gtk.Button(label="Log", valign=Gtk.Align.CENTER)
            logb.connect("clicked", lambda *_: self.open_trouble(scope))
            for b in (logb, stop, launch):
                arow.add_suffix(b)
            ag.add(arow)
            if inst:
                mrow = Adw.ActionRow(title="Remove from app menu",
                                     subtitle="Deletes the installed copy. You choose whether to keep the app data or move it to the Trash.")
                mb = Gtk.Button(label="Uninstall…", valign=Gtk.Align.CENTER)
                mb.add_css_class("destructive-action")
                mb.connect("clicked", lambda *_: self.uninstall(scope))
            else:
                mrow = Adw.ActionRow(title="Add to app menu",
                                     subtitle="Keeps an unpacked copy, so the AppImage file can be deleted.")
                mb = Gtk.Button(label="Install", valign=Gtk.Align.CENTER, sensitive=bool(src and os.path.isfile(src)))
                can_mount = bool(shutil.which("squashfuse") and shutil.which("fusermount3"))
                packrow = Adw.SwitchRow(
                    title="Use less disk space",
                    subtitle="Keeps the app compressed and mounts it at launch. Slower, and the file is read outside "
                             "the sandbox, so only for apps you trust. Experimental. Applies to Install, never to Run once.",
                    active=can_mount and app_mount(scope), sensitive=can_mount)   # remembered per app
                if not can_mount:
                    cmd = mount_install_cmd()
                    packrow.set_subtitle("Needs squashfuse and fuse3. " +
                                         (f"Install them with: {esc(cmd)}" if cmd else "Install them with your package manager."))
                else:
                    packrow.connect("notify::active", lambda r, _p: ui_set(f"mount.{scope}", "1" if r.get_active() else "0"))
                askrow = Adw.SwitchRow(
                    title="Ask before opening this AppImage",
                    subtitle="Offers Install or Run once when you double-click the file. Turn off to run it straight away.",
                    active=app_open_mode(scope) == "ask")
                askrow.connect("notify::active",
                               lambda r, _p: ui_set(f"open_mode.{scope}", "ask" if r.get_active() else "run"))
                mb.connect("clicked", lambda *_: self.install_file(
                    src, None, lambda: (self.nav.pop(), self.open_app(scope)), packrow.get_active()))
            mrow.add_suffix(mb)
            ag.add(mrow)
            if not inst:
                ag.add(packrow)
                ag.add(askrow)
            moved = ""       # the install moved the AppImage into AppImage Jail: offer to get it back without uninstalling
            if kind == "mount" and os.path.isfile(f"{INSTALLED}/{scope}/origin"):
                try:
                    moved = open(f"{INSTALLED}/{scope}/origin").read().strip()
                except OSError:
                    pass
            if moved:
                rrow = Adw.ActionRow(title="Get the AppImage back",
                                     subtitle="Choose a folder to put a copy in. The app stays installed and compressed.")
                rbt = Gtk.Button(label="Restore…", valign=Gtk.Align.CENTER)

                def do_restore(btn):
                    fd = Gtk.FileDialog(title="Choose where to put the AppImage")
                    if os.path.isdir(os.path.dirname(moved)):
                        fd.set_initial_folder(Gio.File.new_for_path(os.path.dirname(moved)))     # where it came from

                    def chosen(d, res):
                        try:
                            folder = d.select_folder_finish(res).get_path()
                        except GLib.Error:
                            return
                        btn.set_sensitive(False)
                        target = os.path.join(folder, os.path.basename(moved))

                        def work():
                            GLib.idle_add(done, run([SELF, "restore", scope, "--keep", "--to", folder]))

                        def done(r):
                            if r.returncode:
                                self.toast(((r.stderr or r.stdout).strip().splitlines() or ["Restore failed"])[-1][:120])
                                btn.set_sensitive(True)
                            else:
                                self.nav.pop()
                                self.open_app(scope)
                                self.toast(f"A copy of the AppImage is at {target}")
                            return False

                        threading.Thread(target=work, daemon=True).start()

                    fd.select_folder(self, None, chosen)

                rbt.connect("clicked", do_restore)
                rrow.add_suffix(rbt)
                ag.add(rrow)
            page.add(ag)

        def add_switch(g, key, t, sub):
            sw = Adw.SwitchRow(title=t, subtitle=sub, active=eff(key))
            rb = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Use the default" if scope else "Use the built-in default")
            rb.add_css_class("flat")
            sw.add_suffix(rb)

            def on_sw(row, _p):
                if syncing[0]:
                    return
                val = int(row.get_active())
                if val == default_for(key):
                    R["s"].pop(key, None)
                else:
                    R["s"][key] = val
                commit()

            def on_reset(_b):
                R["s"].pop(key, None)
                syncing[0] = True
                sw.set_active(eff(key))
                syncing[0] = False
                commit()

            sw.connect("notify::active", on_sw)
            rb.connect("clicked", on_reset)
            marks.append(lambda: rb.set_visible(key in R["s"]))
            rb.set_visible(key in R["s"])
            g.add(sw)

        for gtitle, desc, items in SECTIONS:
            g = Adw.PreferencesGroup(title=gtitle, description=desc or "")
            for key, t, sub in items:
                add_switch(g, key, t, sub)
            page.add(g)

        def eff_num(k):
            v = G["s"].get(k, 0)
            if scope:
                v = A["s"].get(k, v)
            return int(v)

        def default_num(k):
            return int(G["s"].get(k, 0)) if scope else 0

        limg = Adw.PreferencesGroup(
            title="Resource limits",
            description="0 means no limit. Keeps one app from using all your memory or CPU. "
                        "Needs systemd (systemd-run --user); without it the app runs unlimited.")
        for key, t, sub, upper, step in (
                ("mem_max", "Memory limit (MB)", "The app can't use more memory than this.", 262144, 256),
                ("cpu_max", "CPU limit (% of one core)", "100 is one full core, 400 is four.", 6400, 25),
                ("tasks_max", "Process limit", "Most processes and threads the app may have at once.", 100000, 50)):
            sp = Adw.SpinRow(title=t, subtitle=sub, adjustment=Gtk.Adjustment(
                value=eff_num(key), lower=0, upper=upper, step_increment=step, page_increment=step * 10))
            rb = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Use the default" if scope else "Use the built-in default")
            rb.add_css_class("flat")
            sp.add_suffix(rb)

            def on_spin(row, _p, key=key):
                if syncing[0]:
                    return
                val = int(row.get_value())
                if val == default_num(key):
                    R["s"].pop(key, None)
                else:
                    R["s"][key] = val
                commit()

            def on_reset_num(_b, key=key, sp=sp):
                R["s"].pop(key, None)
                syncing[0] = True
                sp.set_value(eff_num(key))
                syncing[0] = False
                commit()

            sp.connect("notify::value", on_spin)
            rb.connect("clicked", on_reset_num)
            marks.append(lambda key=key, rb=rb: rb.set_visible(key in R["s"]))
            rb.set_visible(key in R["s"])
            limg.add(sp)
        page.add(limg)

        def xmap(*srcs):
            m = {}
            for s in srcs:
                for x in s["xdg"]:
                    k, _, v = x.partition(":")
                    m[k] = v
            return m

        xg = Adw.PreferencesGroup(title="Standard folders",
                                  description="Everything else in your home stays hidden from the app.")
        cur = xmap(G, A) if scope else xmap(G)
        for kind, label in XDG:
            v = cur.get(kind)
            row = Adw.ComboRow(title=label, model=Gtk.StringList.new(MODES))
            row.set_selected(2 if v == "" else 1 if v == "ro" else 0)

            def on_xdg(r, _p, kind=kind):
                idx = r.get_selected()
                R["xdg"] = [x for x in R["xdg"] if x.partition(":")[0] != kind]
                if idx == 2:
                    R["xdg"].append(kind)
                elif idx == 1:
                    R["xdg"].append(kind + ":ro")
                elif scope and xmap(G).get(kind) not in (None, "none"):
                    R["xdg"].append(kind + ":none")
                commit()

            row.connect("notify::selected", on_xdg)
            xg.add(row)
        page.add(xg)

        fg = Adw.PreferencesGroup(title="Other folders and files",
                                  description="Folders or files in your home (not hidden ones) or on a mounted drive.")
        hb = Gtk.Box(spacing=6)
        addf = Gtk.Button(label="Add folder", valign=Gtk.Align.CENTER)
        addfile = Gtk.Button(label="Add file", valign=Gtk.Align.CENTER)
        hb.append(addf)
        hb.append(addfile)
        fg.set_header_suffix(hb)
        frows = []

        def refill():
            for r in frows:
                fg.remove(r)
            frows.clear()
            inh = [(p, False) for p in G["allow"]] + [(p, True) for p in G["allow_ro"]] if scope else []
            mine = [(p, False) for p in R["allow"]] + [(p, True) for p in R["allow_ro"]]
            if not inh and not mine:
                r = Adw.ActionRow(title="None", subtitle="The app only sees its own private folder")
                fg.add(r)
                frows.append(r)
            for p, ro in inh:
                r = Adw.ActionRow(title=esc(p), subtitle="From default settings · " + ("read-only" if ro else "read & write"))
                fg.add(r)
                frows.append(r)
            for p, ro in mine:
                r = Adw.ActionRow(title=esc(p))
                tog = Gtk.ToggleButton(label="Read-only", active=ro, valign=Gtk.Align.CENTER)
                tog.connect("toggled", lambda b, path=p: move(path, b.get_active()))
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
                rm.add_css_class("flat")
                rm.connect("clicked", lambda b, path=p: remove(path))
                r.add_suffix(tog)
                r.add_suffix(rm)
                fg.add(r)
                frows.append(r)

        def drop(path):
            for k in ("allow", "allow_ro"):
                if path in R[k]:
                    R[k].remove(path)

        def move(path, ro):
            drop(path)
            R["allow_ro" if ro else "allow"].append(path)
            commit()

        def remove(path):
            drop(path)
            commit()
            refill()

        def pick(folder):
            dlg = Gtk.FileDialog(title="Choose a folder to share" if folder else "Choose a file to share")

            def cb(d, res):
                try:
                    f = d.select_folder_finish(res) if folder else d.open_finish(res)
                except GLib.Error:
                    return
                ok = path_ok(f.get_path())
                if not ok:
                    self.toast("Only non-hidden items in your home, or on a mounted drive, can be shared. Names containing ' #' can't be stored.")
                    return
                drop(ok)
                R["allow"].append(ok)
                commit()
                refill()

            (dlg.select_folder if folder else dlg.open)(self, None, cb)

        addf.connect("clicked", lambda *_: pick(True))
        addfile.connect("clicked", lambda *_: pick(False))
        page.add(fg)

        def list_group(gtitle, desc, key, labels, build):
            lg = Adw.PreferencesGroup(title=gtitle, description=desc)
            addb = Gtk.Button(label="Add", valign=Gtk.Align.CENTER)
            lg.set_header_suffix(addb)
            lrows = []

            def refill_l():
                for r in lrows:
                    lg.remove(r)
                lrows.clear()
                items = [(v, True) for v in (G[key] if scope else [])] + [(v, False) for v in R[key]]
                if not items:
                    r = Adw.ActionRow(title="None")
                    lg.add(r)
                    lrows.append(r)
                for v, inherited in items:
                    r = Adw.ActionRow(title=esc(v))
                    if inherited:
                        r.set_subtitle("From default settings")
                    else:
                        rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
                        rm.add_css_class("flat")
                        rm.connect("clicked", lambda b, v=v: (R[key].remove(v), commit(), refill_l()))
                        r.add_suffix(rm)
                    lg.add(r)
                    lrows.append(r)

            def got(vals):
                v = build(vals)
                if not v or has_comment(v):
                    self.toast("That value is not valid")
                    return
                if v not in R[key]:
                    R[key].append(v)
                commit()
                refill_l()

            addb.connect("clicked", lambda *_: self.ask(gtitle, labels, got))
            refill_l()
            page.add(lg)

        list_group("Environment variables", "Extra variables set for this app.", "env",
                   ["Name", "Value"],
                   lambda v: f"{v[0]}={v[1]}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v[0]) else None)
        # One switch per opt-in portal. A portal enabled in the default settings shows as on on an app's page;
        # turning it off there stores "portal=-NAME" in that app's settings, which undoes the default for this app only.
        pg = Adw.PreferencesGroup(
            title="Extra portals",
            description="Off by default. Each switch lets the app use one more desktop portal. "
                        "Needs Notifications and portals. The desktop may still show its own prompt.")
        for pname in EXTRA_PORTALS:
            ptitle, psub = PORTAL_INFO[pname]
            inherited = bool(scope) and pname in G["portal"]
            psw = Adw.SwitchRow(
                title=ptitle,
                subtitle=psub + ("\nOn in the default settings. Turn it off here for this app only." if inherited else ""),
                active=pname in R["portal"] or (inherited and "-" + pname not in R["portal"]))

            def on_portal(row, _p, pname=pname, inherited=inherited):
                R["portal"] = [x for x in R["portal"] if x not in (pname, "-" + pname)]
                if row.get_active():
                    if not inherited:
                        R["portal"].append(pname)
                elif inherited:
                    R["portal"].append("-" + pname)
                commit()

            psw.connect("notify::active", on_portal)
            pg.add(psw)
        page.add(pg)
        list_group("D-Bus services", "Extra session-bus names the app may talk to (advanced).", "talk",
                   ["Service name, e.g. org.gnome.SessionManager"],
                   lambda v: v[0] if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*(\.\*)?", v[0]) else None)

        if scope:
            dg = Adw.PreferencesGroup(title="Storage")
            home_dir, ext_dir = f"{HOMES}/{scope}", f"{EXTRACT}/{scope}"
            layer_dir, layer_row = f"{LAYERS}/{scope}", [None]      # row shown only once the app has saved into /app
            data_row = Adw.ActionRow(title="App data", subtitle=dir_size(home_dir))
            openb = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
            openb.connect("clicked", lambda *_: (os.makedirs(home_dir, exist_ok=True),
                                                 subprocess.Popen(["xdg-open", home_dir])))
            resetb = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER)
            resetb.add_css_class("destructive-action")

            def do_reset_data():
                if os.path.isdir(home_dir) and not os.path.islink(home_dir):
                    r = run(["gio", "trash", home_dir], timeout=120)
                    if r.returncode:
                        self.toast("Could not move the data to the Trash")
                        return
                if os.path.isdir(f"{LAYERS}/{scope}") and not os.path.islink(f"{LAYERS}/{scope}"):
                    if not trash_layer(f"{LAYERS}/{scope}"):
                        self.toast("Could not move the writable layer to the Trash")
                        return
                    if layer_row[0]: layer_row[0].set_subtitle(dir_size(layer_dir))
                data_row.set_subtitle(dir_size(home_dir))
                self.toast("App data moved to the Trash")

            resetb.connect("clicked", lambda *_: self.confirm(
                f"Move all data of {scope} to the Trash?",
                "Settings, logins and files the app saved, including anything in its writable layer (such as game saves), are moved to the Trash. You can restore them from there.",
                "Move to Trash", do_reset_data))
            data_row.add_suffix(openb)
            data_row.add_suffix(resetb)
            dg.add(data_row)

            if os.path.isdir(layer_dir):
                layer_row[0] = Adw.ActionRow(title="Writable layer", subtitle=dir_size(layer_dir),
                                          tooltip_text="Files the app saved into its own folder (/app), e.g. game saves. "
                                                       "They are under upper/. Removed by Reset… above.")
                openl = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
                openl.connect("clicked", lambda *_: subprocess.Popen(["xdg-open", layer_dir]))
                layer_row[0].add_suffix(openl)
                dg.add(layer_row[0])

            if inst:
                dg.add(Adw.ActionRow(title="Installed copy", subtitle=dir_size(f"{INSTALLED}/{scope}")
                                     + (" · compressed, mounted at launch" if kind == "mount" else "")))
            if not inst or os.path.isdir(ext_dir):
                ext_row = Adw.ActionRow(title="Extracted copy",
                                        subtitle=dir_size(ext_dir) + " · rebuilt on next launch if removed")
                clearb = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)

                def do_clear():
                    if os.path.isdir(ext_dir) and not os.path.islink(ext_dir):
                        shutil.rmtree(ext_dir, ignore_errors=True)
                    ext_row.set_subtitle(dir_size(ext_dir) + " · rebuilt on next launch if removed")

                clearb.connect("clicked", lambda *_: do_clear())
                ext_row.add_suffix(clearb)
                dg.add(ext_row)
            page.add(dg)

        tg = Adw.PreferencesGroup()
        exp = Adw.ExpanderRow(title="Technical details")
        exp.add_row(tech)
        tg.add(exp)
        page.add(tg)

        rg = Adw.PreferencesGroup()
        rbtn = Gtk.Button(label="Reset permissions…", halign=Gtk.Align.CENTER)
        rbtn.add_css_class("destructive-action")

        def do_reset():
            write_cfg(scope, empty())
            self.nav.pop()

        rbtn.connect("clicked", lambda *_: self.confirm(
            "Reset permissions?",
            "All changes for this " + ("app" if scope else "default") + " go back to " +
            ("the defaults." if scope else "the built-in settings."), "Reset", do_reset))
        rg.add(rbtn)
        page.add(rg)

        if scope and not inst:
            forget_group = Adw.PreferencesGroup(description="This app is not installed but still listed because settings or saved data are left. "
                                                  "Forgetting it removes them. The AppImage file is not touched.")
            forget_btn = Gtk.Button(label="Forget this app…", halign=Gtk.Align.CENTER)
            forget_btn.add_css_class("destructive-action")

            def do_forget():
                err = forget_app(scope)
                if err:
                    self.toast(err)
                    return
                self.nav.pop()
                self.fill_apps()
                self.toast(f"{scope} forgotten")

            forget_btn.connect("clicked", lambda *_: self.confirm(
                f"Forget {scope}?",
                "Its settings and remembered identity are removed and its saved data is moved to the Trash "
                "(you can restore it from there).", "Forget", do_forget))
            forget_group.add(forget_btn)
            page.add(forget_group)

        for m in marks:
            m()
        refill()
        refresh_tech()

        header = Adw.HeaderBar()
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        np = Adw.NavigationPage(title=title, child=view)
        np.connect("hidden", lambda *_: self.fill_apps())
        self.nav.push(np)

    def open_trouble(self, scope):
        page = Adw.PreferencesPage()
        hg = Adw.PreferencesGroup(title="Health")
        hl = Gtk.Label(xalign=0, wrap=True, selectable=True, margin_top=6, margin_bottom=6)
        hl.add_css_class("monospace")
        hg.add(hl)
        page.add(hg)

        sg = Adw.PreferencesGroup(title="Suggestions", description="Based on this app's last run, which ended with an error. "
                                  "Nothing changes until you press the button; it applies the next time the app starts.")
        sg.set_visible(False)
        sug_rows = []
        page.add(sg)

        def show_hints(lines):
            for r in sug_rows:
                sg.remove(r)
            sug_rows.clear()
            hints = []
            if scope:
                d = dump(scope)
                hints = log_hints(lines, effective_switches(d["global"], d.get("app")))
            for key, want, title, why in hints:
                row = Adw.ActionRow(title=esc(title), subtitle=esc(why))
                btn = Gtk.Button(label="Turn on" if want else "Turn off", valign=Gtk.Align.CENTER)

                def apply(_b, key=key, want=want):
                    R = dump(scope).get("app") or empty()
                    R["s"][key] = want
                    try:
                        write_cfg(scope, R)
                        self.toast("Saved. It applies the next time the app starts.")
                    except OSError as e:
                        self.toast(f"Could not save: {e.strerror or e}")
                    refresh()

                btn.connect("clicked", apply)
                row.add_suffix(btn)
                sg.add(row)
                sug_rows.append(row)
            sg.set_visible(bool(hints))

        lg = Adw.PreferencesGroup(title="Recent activity" + (f" · {scope}" if scope else ""),
                                  description="Errors from apps appear here. Paste them when asking for help.")
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                          wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8,
                          top_margin=8, bottom_margin=8)
        sw = Gtk.ScrolledWindow(min_content_height=320, child=tv)
        sw.add_css_class("card")
        lg.add(sw)
        page.add(lg)

        def refresh(*_):
            r = run([SELF, "check"])
            hl.set_text((r.stdout + r.stderr).strip() or "No output. Is ajail installed?")
            try:
                lines = open(LOG, errors="replace").readlines()
                if scope:
                    lines = [l for l in lines if f"[{scope}]" in l]
                txt = "".join(lines[-200:]) or "Nothing logged yet."
            except OSError:
                lines, txt = [], "No launches logged yet."
            tv.get_buffer().set_text(txt)
            show_hints(lines)

        header = Adw.HeaderBar()
        rb = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        rb.connect("clicked", refresh)
        header.pack_end(rb)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        refresh()
        self.nav.push(Adw.NavigationPage(title="Troubleshooting", child=view))

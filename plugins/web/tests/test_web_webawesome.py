"""Guard assertions for the Web Awesome migration."""


def test_base_loads_webawesome_assets(web):
    # /login is public-ish (GET, renders base.html head) — no auth needed.
    r = web.client.get("/login")
    assert r.status_code == 200
    html = r.text
    # Web Awesome loaded from the pinned CDN URL; Hearth theme local.
    assert "ka-f.webawesome.com/webawesome@3.8.0" in html
    assert "hearth-theme.css" in html


def test_base_theme_bootstrap_present(web):
    r = web.client.get("/login")
    html = r.text
    # Dual theme state: the data-theme attribute and the wa- class bootstrap.
    assert 'data-theme=' in html
    assert "wa-dark" in html  # referenced by the inline bootstrap/restore script


def _login(web):
    g = web.client.get("/login")
    token = g.text.split('name="csrf_token" value="')[1].split('"')[0]
    web.client.post("/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": token})


def test_admin_shell_uses_wa_page(web):
    _login(web)
    r = web.client.get("/admin")
    assert r.status_code == 200
    assert "<wa-page" in r.text
    # Invariants preserved through the shell:
    assert "data-theme=" in r.text
    assert "toggleTheme" in r.text


def test_login_uses_wa_components_and_keeps_csrf(web):
    r = web.client.get("/login")
    html = r.text
    import re
    assert re.search(r'<input[^>]*type=["\']email["\']', html) and "<wa-button" in html
    # CSRF + field contract intact (auth tests depend on this).
    assert 'name="csrf_token" value="' in html
    assert 'name="email"' in html and 'name="password"' in html
    assert 'action="/login"' in html
    assert 'method="post"' in html.lower()


def test_tables_use_hearth_table_and_keep_data(web):
    _login(web)
    p = web.client.get("/admin/plugins")
    assert 'class="hearth-table"' in p.text and "auth" in p.text
    d = web.client.get("/admin/db")
    assert "_hearth_outbox" in d.text and "Outbox rows" in d.text
    assert "<wa-badge" in d.text  # status cells


def test_dashboard_cards_and_chiplists(web):
    _login(web)
    dash = web.client.get("/admin")
    assert "<wa-card" in dash.text
    assert "wa-grid" in dash.text       # stat cards laid out with the wa-grid utility
    assert "Plugins" in dash.text       # stat label invariant
    ents = web.client.get("/admin/entities")
    assert "<wa-tag" in ents.text
    assert "wa-cluster" in ents.text    # chips wrap via the wa-cluster utility


def test_prefs_js_served(web):
    r = web.client.get("/static/prefs.js")
    assert r.status_code == 200
    assert "setTheme" in r.text
    assert "setDensity" in r.text
    assert "setSidebarTone" in r.text
    assert "setRadius" in r.text
    assert "resetPreferences" in r.text


def test_base_bootstrap_restores_all_dimensions(web):
    r = web.client.get("/login")
    html = r.text
    # Bootstrap script covers all 4 dimensions — check localStorage key strings.
    assert '"density"' in html        # localStorage.getItem("density")
    assert '"sidebar-tone"' in html   # localStorage.getItem("sidebar-tone")
    assert '"radius"' in html         # localStorage.getItem("radius")
    # prefs.js is loaded in body.
    assert 'prefs.js' in html


def test_profile_row_in_admin_shell(web):
    _login(web)
    r = web.client.get("/admin")
    assert r.status_code == 200
    assert 'href="/admin/preferences"' in r.text
    assert 'profile-row' in r.text


def test_preferences_page_renders(web):
    _login(web)
    r = web.client.get("/admin/preferences")
    assert r.status_code == 200
    # All four tile dimensions present.
    assert 'data-pref-dim="theme"' in r.text
    assert 'data-pref-dim="density"' in r.text
    assert 'data-pref-dim="sidebarTone"' in r.text
    assert 'data-pref-dim="radius"' in r.text
    # Reset control present.
    assert 'resetPreferences' in r.text

"""Headless TUI tests — the whole app is driven through Textual's test pilot."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, RichLog, Select, Static, Switch, TabbedContent

from ytkit.config import Config
from ytkit.tui.app import YtkitApp
from ytkit.tui.settings import SettingsPanel, widget_id

pytestmark = pytest.mark.asyncio


def app_with(tmp_path) -> YtkitApp:
    cfg = Config(output_dir=str(tmp_path))
    return YtkitApp(cfg)


async def test_app_mounts_with_all_tabs(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        assert app.query_one("#job-table", DataTable).columns
        assert app.query_one("#settings-panel", SettingsPanel)
        assert app.query_one("#command-view", Static)


async def test_every_config_field_renders_a_widget(tmp_path):
    """The settings form is generated — this catches an unrenderable field kind."""
    async with app_with(tmp_path).run_test() as pilot:
        panel = pilot.app.query_one("#settings-panel", SettingsPanel)
        for name, _ in Config.spec():
            assert panel.query(f"#{widget_id(name)}"), f"no widget for {name}"


async def test_toggling_a_switch_updates_the_config_and_command(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        switch = app.query_one(f"#{widget_id('embed_subs')}", Switch)
        assert app.cfg.embed_subs is False
        switch.value = True
        await pilot.pause()
        assert app.cfg.embed_subs is True
        assert "--embed-subs" in str(app.query_one("#command-view", Static).content)


async def test_editing_an_input_updates_the_config(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        field = app.query_one(f"#{widget_id('sub_langs')}", Input)
        field.value = "en,fr,de"
        await pilot.pause()
        assert app.cfg.sub_langs == ["en", "fr", "de"]


async def test_invalid_number_leaves_config_untouched(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        field = app.query_one(f"#{widget_id('max_fps')}", Input)
        before = app.cfg.max_fps
        field.value = "-"
        await pilot.pause()
        assert app.cfg.max_fps == before


async def test_choice_field_updates_config(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.query_one(f"#{widget_id('container')}", Select).value = "mkv"
        await pilot.pause()
        assert app.cfg.container == "mkv"


async def test_preset_selection_rewrites_settings_widgets(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.query_one("#preset-select", Select).value = "audio"
        await pilot.pause()
        assert app.cfg.mode == "audio"
        # The generated form must re-sync, not drift from the config.
        assert app.query_one(f"#{widget_id('mode')}", Select).value == "audio"
        assert app.query_one(f"#{widget_id('audio_format')}", Select).value == "mp3"
        assert "Audio only" in str(app.query_one("#preset-hint", Static).content)


async def test_adding_a_url_creates_a_queue_row(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        field = app.query_one("#url-input", Input)
        field.value = "https://example.com/watch?v=x"
        await pilot.press("enter")
        await pilot.pause()
        assert field.value == ""
        assert len(app.engine.jobs) == 1
        log = "\n".join(strip.text for strip in app.query_one("#log", RichLog).lines)
        assert "queued https://example.com/watch?v=x" in log


async def test_multiple_urls_in_one_submission(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.query_one("#url-input", Input).value = "https://a.test/1 https://b.test/2"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.engine.jobs) == 2


async def test_job_rows_render_after_event_drain(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.add_url("https://example.com/watch?v=y")
        app._drain_events()
        await pilot.pause()
        assert app.query_one("#job-table", DataTable).row_count == 1


async def test_bindings_are_reachable(tmp_path):
    """Every declared binding must map to an existing action."""
    async with app_with(tmp_path).run_test() as pilot:
        for binding in YtkitApp.BINDINGS:
            action = binding[1]
            assert hasattr(pilot.app, f"action_{action}"), f"missing action_{action}"


async def test_save_config_action_writes_file(tmp_path, monkeypatch):
    import ytkit.storage as storage
    import ytkit.tui.app as tui_app

    target = tmp_path / "config.toml"
    monkeypatch.setattr(tui_app, "save_config", lambda cfg: storage.save_config(cfg, target))
    async with app_with(tmp_path).run_test() as pilot:
        pilot.app.action_save_config()
        assert target.is_file()
        assert "output_dir" in target.read_text()


async def test_queue_row_updates_in_place(tmp_path):
    """Progress updates go through update_cell, which needs real column keys."""
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        app.add_url("https://example.com/watch?v=z")
        job = next(iter(app.engine.jobs.values()))

        app._render_row(job)  # insert
        table = app.query_one("#job-table", DataTable)
        assert table.row_count == 1

        job.title, job.percent, job.speed = "Updated Title", 42.0, "1.2MiB/s"
        app._render_row(job)  # update — must not raise, must not add a row
        await pilot.pause()

        assert table.row_count == 1
        assert "Updated Title" in str(table.get_cell(str(job.id), "title"))
        assert table.get_cell(str(job.id), "speed") == "1.2MiB/s"


async def test_clicking_a_tab_switches_panes(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        await pilot.pause()
        tabs = pilot.app.query_one("#tabs", TabbedContent)
        for tab in ("tab-settings", "tab-command", "tab-queue"):
            await pilot.click(f"Tab#--content-tab-{tab}")
            await pilot.pause()
            assert tabs.active == tab


async def test_show_command_binding_switches_to_the_command_tab(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        app.set_focus(None)  # otherwise the URL input swallows the keypress
        await pilot.press("c")
        await pilot.pause()
        assert app.query_one("#tabs", TabbedContent).active == "tab-command"
        assert "yt-dlp" in str(app.query_one("#command-view", Static).content)


async def test_add_url_binding_returns_to_the_queue_tab(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.click("Tab#--content-tab-tab-settings")
        await pilot.pause()
        app.set_focus(None)
        await pilot.press("a")
        await pilot.pause()
        assert app.query_one("#tabs", TabbedContent).active == "tab-queue"
        assert app.focused is app.query_one("#url-input", Input)


@pytest.mark.parametrize(
    "width,expected_min",
    [(80, 18), (100, 24), (140, 60), (220, 70)],
)
async def test_title_column_fills_available_width(tmp_path, width, expected_min):
    """DataTable has no flex columns, so the title width is computed per resize."""
    async with app_with(tmp_path).run_test(size=(width, 30)) as pilot:
        app = pilot.app
        await pilot.pause()
        app._fit_title_column()
        assert app.title_width >= expected_min
        assert app.title_width <= 70  # MAX_TITLE, so wide terminals stay readable


async def test_table_columns_fit_without_horizontal_scroll(tmp_path):
    from ytkit.tui.app import CELL_PADDING, FIXED_WIDTH

    async with app_with(tmp_path).run_test(size=(100, 30)) as pilot:
        app = pilot.app
        await pilot.pause()
        app._fit_title_column()
        table = app.query_one("#job-table", DataTable)
        total = FIXED_WIDTH + app.title_width + CELL_PADDING
        assert total <= table.size.width


async def test_long_titles_are_truncated_to_the_column(tmp_path):
    async with app_with(tmp_path).run_test(size=(100, 30)) as pilot:
        app = pilot.app
        await pilot.pause()
        app._fit_title_column()
        app.add_url("https://example.com/x")
        job = next(iter(app.engine.jobs.values()))
        job.title = "A" * 300
        app._render_row(job)
        await pilot.pause()
        table = app.query_one("#job-table", DataTable)
        assert len(str(table.get_cell(str(job.id), "title"))) <= app.title_width


async def test_interval_callbacks_survive_an_unmounted_tree(tmp_path):
    """Timers keep firing during startup and teardown, when widgets are absent."""
    app = app_with(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
    # Outside the context the widget tree is gone; the timers must not explode.
    app._fit_title_column()
    app._refresh_status()
    app.log_line("still fine")
    app.refresh_command()
    assert app.selected_job() is None

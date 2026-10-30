"""Headless TUI tests — the whole app is driven through Textual's test pilot."""

from __future__ import annotations

import pytest
from textual.binding import Binding
from textual.widgets import DataTable, Input, Label, RichLog, Select, Static, Switch, TabbedContent

from ytkit.config import Config
from ytkit.engine import Event, Job, State
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


async def test_editing_an_input_updates_the_config_on_enter(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        field = app.query_one(f"#{widget_id('sub_langs')}", Input)
        app.action_show_tab("tab-settings")
        field.focus()
        field.value = "en,fr,de"
        await pilot.pause()
        assert app.cfg.sub_langs == ["en"]  # not while typing
        await pilot.press("enter")
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
            action = binding.action if isinstance(binding, Binding) else binding[1]
            action = action.split("(")[0]
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


# -- Select blank handling ----------------------------------------------------


async def test_clearing_the_preset_picker_is_a_safe_no_op(tmp_path):
    """Clearing back to "--" must not revert settings or raise."""
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        picker = app.query_one("#preset-select", Select)
        picker.value = "audio"
        await pilot.pause()
        assert app.cfg.mode == "audio"

        picker.clear()  # what the UI does when the user selects "--"
        await pilot.pause()
        assert app.cfg.mode == "audio"  # settings survive


async def test_clearing_the_preset_after_manual_edits_keeps_them(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.query_one("#preset-select", Select).value = "mobile"
        await pilot.pause()
        app.query_one(f"#{widget_id('video_quality')}", Select).value = "480"
        await pilot.pause()

        app.query_one("#preset-select", Select).clear()
        await pilot.pause()
        assert app.cfg.video_quality == "480"


async def test_is_blank_matches_the_real_sentinel_not_the_bool():
    """Textual 8: Select.NULL is the sentinel; Select.BLANK is the bool False."""
    from ytkit.tui.app import is_blank

    assert is_blank(Select.NULL) is True
    assert is_blank(False) is False  # the trap — a Switch turned off
    assert is_blank(None) is False
    assert is_blank("audio") is False


async def test_switch_can_be_turned_off(tmp_path):
    """Guarding with `is Select.BLANK` swallowed every switch-off, since
    Select.BLANK is the bool False."""
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        switch = app.query_one(f"#{widget_id('subtitles')}", Switch)
        assert app.cfg.subtitles is True
        switch.value = False
        await pilot.pause()
        assert app.cfg.subtitles is False
        assert "--write-subs" not in str(app.query_one("#command-view", Static).content)


async def test_every_boolean_setting_can_be_toggled_off(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        enabled = [n for n, f in Config.spec()
                   if f.metadata["kind"] == "bool" and getattr(app.cfg, n)]
        assert enabled, "expected some booleans on by default"
        for name in enabled:
            app.query_one(f"#{widget_id(name)}", Switch).value = False
        await pilot.pause()
        still_on = [n for n in enabled if getattr(app.cfg, n)]
        assert not still_on, f"could not turn off: {still_on}"


# -- keyboard reach -------------------------------------------------------------


async def test_show_command_works_while_a_job_is_selected(tmp_path):
    """Focus left in the queue used to drag the view straight back to it."""
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.add_url("https://example.com/watch?v=c")
        app._drain_events()
        await pilot.pause()
        app.query_one("#job-table", DataTable).focus()
        await pilot.press("3")
        await pilot.pause(0.2)
        assert app.query_one("#tabs", TabbedContent).active == "tab-command"


async def test_copy_from_the_table_uses_the_selected_job(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.add_url("https://example.com/watch?v=copied")
        app._drain_events()
        await pilot.pause()
        table = app.query_one("#job-table", DataTable)
        table.focus()
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard.startswith("yt-dlp ")
        assert app.clipboard.endswith("'https://example.com/watch?v=copied'")
        # Stays put, so the next job's command is one keypress away.
        assert app.query_one("#tabs", TabbedContent).active == "tab-queue"
        assert app.focused is table


async def test_copy_elsewhere_uses_the_current_settings(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.add_url("https://example.com/watch?v=ignored")
        app._drain_events()
        await pilot.pause()
        app.action_show_tab("tab-settings")
        await pilot.pause()
        await pilot.press("c")
        assert app.clipboard.endswith("'<URL>'")


@pytest.mark.parametrize("key,tab", [("2", "tab-settings"), ("3", "tab-command"), ("1", "tab-queue")])
async def test_number_keys_switch_tabs_and_stay(tmp_path, key, tab):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        app.query_one("#job-table", DataTable).focus()
        if key == "1":
            await pilot.press("3")
        await pilot.press(key)
        await pilot.pause(0.2)
        assert app.query_one("#tabs", TabbedContent).active == tab


async def test_escape_leaves_the_url_box_for_the_shortcuts(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        assert isinstance(app.focused, Input)
        await pilot.press("escape", "2")
        await pilot.pause(0.2)
        assert app.query_one("#url-input", Input).value == ""
        assert app.query_one("#tabs", TabbedContent).active == "tab-settings"


async def test_p_opens_profiles(tmp_path):
    from ytkit.tui.modals import ProfileScreen

    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.press("escape", "p")
        await pilot.pause()
        assert isinstance(app.screen, ProfileScreen)


async def test_path_settings_complete(tmp_path):
    from ytkit.tui.paths import PathInput

    async with app_with(tmp_path).run_test() as pilot:
        panel = pilot.app.query_one("#settings-panel", SettingsPanel)
        for name in ("output_dir", "temp_dir", "archive_file", "cookies_file", "ffmpeg_location"):
            field = panel.query_one(f"#{widget_id(name)}")
            assert isinstance(field, PathInput), name
        assert panel.query_one(f"#{widget_id('output_dir')}", PathInput).suggester.dirs_only
        assert not panel.query_one(f"#{widget_id('cookies_file')}", PathInput).suggester.dirs_only


# -- settings: commit, validation, markers, filter -------------------------------


async def test_half_typed_value_never_reaches_the_config(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.action_show_tab("tab-settings")
        field = app.query_one(f"#{widget_id('output_dir')}", Input)
        field.focus()
        field.value = "~/Do"
        await pilot.pause()
        assert app.cfg.output_dir == str(tmp_path)


async def test_leaving_a_field_commits_it(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.action_show_tab("tab-settings")
        field = app.query_one(f"#{widget_id('max_fps')}", Input)
        field.focus()
        field.value = "30"
        await pilot.press("escape")
        await pilot.pause()
        assert app.cfg.max_fps == 30


async def test_invalid_value_is_flagged_and_not_applied(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.action_show_tab("tab-settings")
        field = app.query_one(f"#{widget_id('max_fps')}", Input)
        field.focus()
        field.value = "-"
        await pilot.pause()
        assert field.has_class("-invalid")  # flagged while typing
        await pilot.press("enter")
        await pilot.pause()
        assert app.cfg.max_fps == 0
        assert any("Max FPS" in str(n.message) for n in app._notifications)
        field.value = "24"
        await pilot.press("enter")
        await pilot.pause()
        assert not field.has_class("-invalid")


async def test_changed_settings_are_marked(tmp_path):
    from ytkit.tui.settings import CHANGED_MARK, FieldRow

    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        select = app.query_one(f"#{widget_id('container')}", Select)
        row = select.parent
        assert isinstance(row, FieldRow)
        assert not row.has_class("-changed")
        select.value = "mkv"
        await pilot.pause()
        assert row.has_class("-changed")
        assert str(row.query_one(".field-label", Label).content).startswith(CHANGED_MARK)
        select.value = Config.default_for("container")
        await pilot.pause()
        assert not row.has_class("-changed")


async def test_unsaved_changes_show_on_the_tab_until_saved(tmp_path, monkeypatch):
    import ytkit.tui.app as tui_app

    monkeypatch.setattr(tui_app, "save_config", lambda cfg: tmp_path / "config.toml")
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        tab = app.query_one("#tabs", TabbedContent).get_tab("tab-settings")
        assert "*" not in str(tab.label)
        app.query_one(f"#{widget_id('container')}", Select).value = "mkv"
        await pilot.pause()
        assert "*" in str(tab.label)
        app.action_save_config()
        await pilot.pause()
        assert "*" not in str(tab.label)


async def test_saving_commits_the_field_being_edited(tmp_path, monkeypatch):
    import ytkit.tui.app as tui_app

    saved = []
    monkeypatch.setattr(tui_app, "save_config", lambda cfg: saved.append(cfg.max_fps) or tmp_path)
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.action_show_tab("tab-settings")
        field = app.query_one(f"#{widget_id('max_fps')}", Input)
        field.focus()
        field.value = "60"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert saved == [60]


async def test_filter_narrows_the_settings_and_restores_sections(tmp_path):
    from textual.widgets import Collapsible

    from ytkit.tui.settings import FieldRow

    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        panel = app.query_one("#settings-panel", SettingsPanel)
        before = {s: s.collapsed for s in panel.query(Collapsible)}
        panel.filter("cookie")
        await pilot.pause()
        shown = [r.field.name for r in panel.query(FieldRow) if r.display]
        assert shown and all("cookie" in name for name in shown)
        assert all(not s.collapsed for s in panel.query(Collapsible) if s.display)
        panel.filter("")
        await pilot.pause()
        assert all(r.display for r in panel.query(FieldRow))
        assert {s: s.collapsed for s in panel.query(Collapsible)} == before


async def test_slash_jumps_to_the_filter(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.press("escape", "slash")
        await pilot.pause(0.2)
        assert app.query_one("#tabs", TabbedContent).active == "tab-settings"
        assert app.focused is app.query_one("#settings-filter", Input)


# -- queue: duplicates, removal, failures, folders --------------------------------


def place(app: YtkitApp, state: State, **fields) -> Job:
    """Put a job straight into a given state, without running it."""
    job = Job(url=fields.pop("url", f"https://example.com/{state}"), config=app.cfg.copy(), state=state, **fields)
    app.engine.jobs[job.id] = job
    app._render_row(job)
    return job


async def test_a_url_already_in_progress_is_not_queued_twice(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        running = place(app, State.RUNNING, url="https://example.com/same")
        app.add_url("https://example.com/same")
        assert len(app.engine.jobs) == 1
        running.state = State.DONE
        app.add_url("https://example.com/same")  # finished: fetching again is a choice
        assert len(app.engine.jobs) == 2


async def test_x_cancels_running_and_removes_finished(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        table = app.query_one("#job-table", DataTable)
        done = place(app, State.DONE)
        running = place(app, State.QUEUED)
        await pilot.pause()
        table.focus()
        table.move_cursor(row=table.get_row_index(str(done.id)))
        await pilot.press("x")
        await pilot.pause()
        assert done.id not in app.engine.jobs
        assert table.row_count == 1
        table.move_cursor(row=table.get_row_index(str(running.id)))
        await pilot.press("x")
        await pilot.pause()
        assert running.id in app.engine.jobs  # cancelled, not removed


async def test_footer_offers_the_action_that_fits_the_row(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        table = app.query_one("#job-table", DataTable)
        place(app, State.FAILED, error="ERROR: boom")
        await pilot.pause()
        table.focus()
        app.refresh_bindings()
        await pilot.pause()
        shown = {b.binding.description for b in app.active_bindings.values() if b.enabled}
        assert {"Remove", "Retry"} <= shown
        assert "Cancel" not in shown


async def test_clear_finished_keeps_running_jobs(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        place(app, State.DONE)
        place(app, State.FAILED)
        running = place(app, State.RUNNING)
        app.action_clear_finished()
        await pilot.pause()
        assert list(app.engine.jobs) == [running.id]
        assert app.query_one("#job-table", DataTable).row_count == 1


async def test_late_events_do_not_revive_a_removed_row(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        job = place(app, State.DONE)
        app._forget(job.id)
        app.events.put(Event("state", job))
        app._drain_events()
        await pilot.pause()
        assert app.query_one("#job-table", DataTable).row_count == 0


async def test_failed_row_says_why(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        job = place(
            app,
            State.FAILED,
            error="ERROR: [youtube] jNQXAC9IVRw: Private video. Sign in if you've been granted access",
        )
        await pilot.pause()
        cell = app.query_one("#job-table", DataTable).get_cell(str(job.id), "progress")
        assert str(cell) == "Private video"


async def test_open_folder_from_a_job_opens_its_own(tmp_path, monkeypatch):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        opened = []
        monkeypatch.setattr(app, "_open", opened.append)
        media = tmp_path / "Channel" / "video.mp4"
        media.parent.mkdir()
        media.write_text("")
        place(app, State.DONE, files=[media])
        await pilot.pause()
        app.query_one("#job-table", DataTable).focus()
        await pilot.press("o")
        assert opened == [media.parent]


# -- review fixes ----------------------------------------------------------------


async def test_job_keys_do_nothing_off_the_queue_tab(tmp_path):
    """x would otherwise remove or cancel a job the user can't see."""
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        done = place(app, State.DONE)
        await pilot.pause()
        await pilot.press("escape", "2")
        await pilot.pause(0.2)
        await pilot.press("x")
        await pilot.pause()
        assert done.id in app.engine.jobs


async def test_same_url_with_other_settings_is_a_new_job(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        place(app, State.RUNNING, url="https://example.com/same")
        app.cfg.mode = "audio"
        app.add_url("https://example.com/same")
        assert len(app.engine.jobs) == 2


async def test_unsaved_mark_compares_against_the_saved_file(tmp_path):
    cfg = Config(output_dir=str(tmp_path), mode="audio")  # as if launched with --preset audio
    async with YtkitApp(cfg, saved=Config(output_dir=str(tmp_path))).run_test() as pilot:
        await pilot.pause()
        tab = pilot.app.query_one("#tabs", TabbedContent).get_tab("tab-settings")
        assert "*" in str(tab.label)


async def test_a_bad_value_is_warned_about_once(tmp_path):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        app.action_show_tab("tab-settings")
        field = app.query_one(f"#{widget_id('max_fps')}", Input)
        field.focus()
        field.value = "-"
        await pilot.press("enter")
        await pilot.pause()
        app.set_focus(None)  # leaving commits again
        await pilot.pause()
        warnings = [n for n in app._notifications if "Max FPS" in str(n.message)]
        assert len(warnings) == 1


async def test_open_folder_for_a_job_without_files_uses_its_own_settings(tmp_path, monkeypatch):
    async with app_with(tmp_path).run_test() as pilot:
        app = pilot.app
        opened = []
        monkeypatch.setattr(app, "_open", opened.append)
        job = place(app, State.FAILED)
        job.config.output_dir = str(tmp_path / "theirs")
        await pilot.pause()
        app.query_one("#job-table", DataTable).focus()
        await pilot.press("o")
        assert opened == [tmp_path / "theirs"]

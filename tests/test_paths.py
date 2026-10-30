"""Path completion in the settings form."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from ytkit.tui.paths import PathInput, PathSuggester

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "Music").mkdir()
    (tmp_path / "Movies").mkdir()
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos.txt").write_text("")
    (tmp_path / "cookies.txt").write_text("")
    (tmp_path / ".hidden").mkdir()
    return tmp_path


async def suggest(value: str, dirs_only: bool = False) -> str | None:
    return await PathSuggester(dirs_only=dirs_only).get_suggestion(value)


async def test_unique_folder_completes_with_a_trailing_slash(tree):
    assert await suggest(f"{tree}/Mu") == f"{tree}/Music/"


async def test_unique_file_completes_without_a_slash(tree):
    assert await suggest(f"{tree}/coo") == f"{tree}/cookies.txt"


async def test_matches_complete_as_far_as_they_agree(tree):
    assert await suggest(f"{tree}/v") == f"{tree}/videos"


async def test_disagreeing_matches_suggest_nothing(tree):
    # "Movies/" and "Music/" agree on nothing past "M"; a shell stops there too.
    assert await suggest(f"{tree}/M") is None
    assert await suggest(f"{tree}/") is None


async def test_trailing_slash_completes_a_lone_child(tree):
    (tree / "Music" / "Albums").mkdir()
    assert await suggest(f"{tree}/Music/") == f"{tree}/Music/Albums/"


async def test_bare_tilde_becomes_home():
    assert await suggest("~") == "~/"


async def test_unreadable_entries_are_skipped(tree, monkeypatch):
    calls = []

    class Entry:
        def __init__(self, name, fails):
            self.name, self.fails = name, fails

        def is_dir(self):
            calls.append(self.name)
            if self.fails:
                raise PermissionError(self.name)
            return True

    class Scan:
        def __enter__(self):
            return iter([Entry("Locked", True), Entry("Lovely", False), Entry("zz", True)])

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("ytkit.tui.paths.os.scandir", lambda path: Scan())
    assert await suggest(f"{tree}/Lo") == f"{tree}/Lovely/"
    assert "zz" not in calls  # filtered by name before touching the disk


async def test_dirs_only_skips_files(tree):
    assert await suggest(f"{tree}/v", dirs_only=True) == f"{tree}/videos/"
    assert await suggest(f"{tree}/coo", dirs_only=True) is None


async def test_matching_is_case_sensitive(tree):
    assert await suggest(f"{tree}/mu") is None


async def test_hidden_entries_need_a_leading_dot(tree):
    assert await suggest(f"{tree}/") != f"{tree}/.hidden/"
    assert await suggest(f"{tree}/.h") == f"{tree}/.hidden/"


async def test_tilde_is_kept_as_typed(tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree))
    assert await suggest("~/Mu") == "~/Music/"


async def test_relative_paths_complete_against_the_working_directory(tree, monkeypatch):
    monkeypatch.chdir(tree)
    assert await suggest("Mu") == "Music/"


async def test_missing_folder_suggests_nothing(tree):
    assert await suggest(f"{tree}/nowhere/x") is None


class PathApp(App[None]):
    def compose(self) -> ComposeResult:
        yield PathInput(id="path")
        yield Input(id="next")


async def test_tab_accepts_the_suggestion(tree):
    async with PathApp().run_test() as pilot:
        field = pilot.app.query_one("#path", PathInput)
        field.focus()
        field.value = f"{tree}/Mu"
        field.cursor_position = len(field.value)
        await pilot.pause(0.1)
        await pilot.press("tab")
        assert field.value == f"{tree}/Music/"
        assert field.has_focus


async def test_tab_through_an_unedited_field_leaves_it_alone(tree):
    """Tabbing past a setting must not complete it, however completable it is."""
    (tree / "Music" / "Albums").mkdir()
    app = PathApp()
    async with app.run_test() as pilot:
        field = app.query_one("#path", PathInput)
        app.query_one("#next", Input).focus()  # the path box takes focus at mount
        field.value = f"{tree}/Music"
        await pilot.pause()
        field.focus()
        await pilot.pause(0.2)
        await pilot.press("tab")
        assert field.value == f"{tree}/Music"
        assert app.focused is app.query_one("#next", Input)


async def test_tab_without_a_suggestion_moves_focus(tree):
    async with PathApp().run_test() as pilot:
        field = pilot.app.query_one("#path", PathInput)
        field.focus()
        field.value = f"{tree}/zz"
        field.cursor_position = len(field.value)
        await pilot.pause(0.1)
        await pilot.press("tab")
        assert pilot.app.focused is pilot.app.query_one("#next", Input)

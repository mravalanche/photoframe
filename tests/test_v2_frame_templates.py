"""Focused render checks for the Frame and Photos workspace variants."""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from photoframe.models import AppSettings, Photo
from photoframe.renderer import RenderPhase, RenderState

TEMPLATES = Path(__file__).resolve().parents[1] / "src/photoframe/templates"


def _render_workspace(view: str) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    settings = AppSettings()
    settings.frame.album_id = "album"
    settings.frame.album_name = "Family"
    photo = Photo(id="photo", filename="family.jpg", width=1200, height=800)
    framing = settings.frame.preference(photo.id)
    context = {
        "view": view,
        "settings_section": None,
        "notice": "Saved",
        "error": None,
        "settings": settings,
        "displayed_photo": photo,
        "source_name": "Demo library",
        "source_detail": "Demo photos",
        "render_state": RenderState(),
        "render_intent_matches": False,
        "render_completion_visible": False,
        "render_ack_delay_ms": 0,
        "selected_photo": photo,
        "selected_framing": framing,
        "selected_rotation_eligible": True,
        "just_hidden_photo": None,
        "scheduled_transition_soon": False,
        "selection": SimpleNamespace(photo=photo),
        "next_change": "Tomorrow",
        "display_snapshot_available": False,
        "thumbnail_revision": "",
        "schedule_summary": "Daily",
        "schedule_order": "Album order",
        "next_photo_disabled_reason": None,
        "next_request_id": "request",
        "eligible": [photo],
        "hidden_photos": [],
        "browse_photos": [photo],
        "layout_rows": [
            {
                "photo": photo,
                "framing": framing,
                "custom": False,
                "needs_framing": False,
                "unavailable": False,
            }
        ],
        "wrong_orientation_count": 0,
        "unsupported_count": 0,
        "albums": [],
        "current_album_available": True,
        "photo_layout_label": lambda _: "Fill frame",
        "display_summary": "Daily",
        "weekday_names": ["Monday"],
        "RenderPhase": RenderPhase,
        "app_version": "2.0",
        "demo_mode": False,
        "render_phases": [],
        "render_photo": None,
        "next_photo": None,
        "seconds_until_change": None,
    }
    return env.get_template("_workspace.html").render(context)


def test_frame_view_prioritizes_display_and_compact_library_link():
    html = _render_workspace("frame")
    assert 'id="frame-status"' in html
    assert 'action="/photo/next"' in html
    assert 'href="/photos"' in html
    assert 'id="frame-settings"' in html
    assert 'id="photo-workspace"' not in html


def test_photos_view_keeps_editor_and_table_together_with_feedback():
    html = _render_workspace("photos")
    assert 'id="photo-workspace"' in html
    assert 'action="/photo/framing"' in html
    assert 'id="photo-layout-table"' in html
    assert 'hx-select="#photo-workspace"' in html
    assert 'hx-select-oob="#workspace-notices"' in html
    assert 'id="workspace-notices"' in html
    assert 'id="frame-status"' not in html

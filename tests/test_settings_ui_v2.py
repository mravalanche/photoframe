from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from photoframe.models import AppSettings

TEMPLATES = Path(__file__).parents[1] / "src" / "photoframe" / "templates"


class FormNesting(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.nested |= self.depth > 0
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.depth -= 1


def render(name: str) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return env.get_template(name).render(
        settings=AppSettings(),
        credential_saved=False,
        demo_mode=False,
        network_summary="This device only",
        network_address="http://127.0.0.1:8000",
        version="2.0.0",
    )


def test_settings_sections_render_shared_controls_with_existing_form_contracts() -> None:
    provider = render("_settings_provider.html")
    hardware = render("_settings_hardware.html")
    advanced = render("_settings_advanced.html")
    reset = render("_reset_dialog.html")
    assert 'data-settings-panel="provider"' in provider
    assert 'name="api_key"' in provider
    assert 'action="/connection"' in provider
    assert 'name="display_width_px"' in hardware
    assert 'name="render_timeout_seconds"' in hardware
    assert 'action="/hardware"' in hardware
    for hook in (
        'data-network-form',
        'data-certificate-controls',
        'data-supplied-certificate-controls',
        'data-address-preview',
    ):
        assert hook in advanced
    assert 'name="confirm_endpoint_change"' in advanced
    assert 'data-reset-dialog' in reset
    assert 'data-reset-form' in reset
    for html in (provider, hardware, advanced, reset):
        assert 'class="btn ' in html
        parser = FormNesting()
        parser.feed(html)
        assert not parser.nested


def test_updates_keep_every_script_hook_and_actions_together() -> None:
    html = render("_updates.html")
    for hook in (
        'id="installed-version"',
        'id="update-status-badge"',
        'data-update-icon',
        'data-update-label',
        'id="update-status"',
        'id="update-progress"',
        'id="update-error"',
        'id="managed-controls"',
        'id="check-update"',
        'id="stage-update"',
        'id="apply-update"',
        'id="rollback-update"',
        'id="update-channel"',
        'id="weekly-checks"',
        'id="apply-dialog"',
        'id="confirm-action"',
        'id="release-notes-section"',
    ):
        assert hook in html
    assert html.index('id="update-status"') < html.index('id="managed-controls"')
    parser = FormNesting()
    parser.feed(html)
    assert not parser.nested

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from photoframe.web import create_app

TEMPLATES = Path(__file__).parents[1] / "src" / "photoframe" / "templates"


def environment() -> Environment:
    return Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


class Structure(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.inputs: list[str] = []
        self.labels: list[str] = []
        self.form_depth = 0
        self.nested_forms = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if tag in ("input", "select") and attributes.get("id"):
            self.inputs.append(attributes["id"] or "")
        if tag == "label" and attributes.get("for"):
            self.labels.append(attributes["for"] or "")
        if tag == "form":
            if self.form_depth:
                self.nested_forms = True
            self.form_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.form_depth -= 1


def test_showcase_renders_shared_components_in_both_themes() -> None:
    template = environment().get_template("showcase.html")
    for theme in ("photoframe-light", "photoframe-dark"):
        html = template.render(theme=theme)
        structure = Structure()
        structure.feed(html)
        assert f'data-theme="{theme}"' in html
        assert 'class="collapse collapse-arrow' in html
        assert 'class="table w-full table-zebra' in html
        assert 'role="table"' in html
        assert "border-info bg-info/15" not in html
        assert 'class="modal"' in html
        assert 'aria-current="step"' in html
        assert len(structure.ids) == len(set(structure.ids))
        assert set(structure.inputs).issubset(structure.labels)
        assert not structure.nested_forms


def test_component_attribute_values_are_escaped() -> None:
    html = (
        environment()
        .from_string(
            '{% import "components/actions.html" as actions %}'
            '{{ actions.button("Preview", attrs={"aria-label": "<unsafe>"}) }}'
        )
        .render()
    )
    assert 'aria-label="&lt;unsafe&gt;"' in html
    assert "<unsafe>" not in html


def test_surface_components_accept_outer_interaction_hooks() -> None:
    html = (
        environment()
        .from_string(
            '{% import "components/surfaces.html" as surfaces %}'
            '{% call surfaces.card("Frame", attrs={"data-frame": "ready"}) %}Photo{% endcall %}'
            '{% call surfaces.disclosure("Album", attrs={"data-settings-panel": "album"}) %}'
            "Choices{% endcall %}"
        )
        .render()
    )
    assert 'data-frame="ready"' in html
    assert 'data-settings-panel="album"' in html


def test_showcase_route_uses_only_the_two_supported_themes(tmp_path: Path) -> None:
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    assert 'data-theme="photoframe-dark"' in client.get("/ui/showcase?theme=photoframe-dark").text
    response = client.get("/ui/showcase?theme=unknown")
    assert response.status_code == 200
    assert 'data-theme="photoframe-light"' in response.text
    assert client.get("/static/v2.css").status_code == 200

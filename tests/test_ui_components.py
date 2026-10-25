from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

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
        assert 'class="table table-zebra"' in html
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

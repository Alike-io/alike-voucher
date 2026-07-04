"""Alike voucher renderer.

Data model → Jinja2 template → WeasyPrint PDF. All paths passed as
absolute file:// URLs (WeasyPrint requirement per project SOP).
"""
from __future__ import annotations
import os, pathlib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from brand import gradient_for, ORANGE, INK

HERE = pathlib.Path(__file__).parent.resolve()
TEMPLATE_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"


def _file_url(p: pathlib.Path | str) -> str:
    return "file://" + str(pathlib.Path(p).resolve())


def render_voucher(data: dict, out_path: str) -> str:
    """Render a voucher dict to a PDF at out_path. Returns the resolved path."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template("voucher.html")

    destination = data["trip"]["destination"]
    grad = gradient_for(destination)

    # Resolve asset paths to file:// URLs so WeasyPrint can find them
    def resolve_thumbs(entity, key="thumb"):
        v = entity.get(key)
        if v and not v.startswith(("http://", "https://", "file://")):
            entity[key] = _file_url(v)

    for h in data["trip"].get("hotels", []):
        resolve_thumbs(h)
    for day in data["trip"].get("days", []):
        for stop in day.get("stops", []):
            stop["thumbs"] = [_file_url(t) if not t.startswith(("http", "file")) else t
                              for t in stop.get("thumbs", [])]

    ctx = {
        **data,
        "orange": ORANGE,
        "ink":    INK,
        "grad1":  grad[0],
        "grad2":  grad[1],
        "grad3":  grad[2],
        "logo_white":              _file_url(STATIC_DIR / "img" / "alike_white_transparent.png"),
        "logo_white_transparent":  _file_url(STATIC_DIR / "img" / "alike_white_transparent.png"),
        "logo_black_transparent":  _file_url(STATIC_DIR / "img" / "alike_black_transparent.png"),
        "font_dir":                _file_url(STATIC_DIR / "fonts"),
    }
    html_str = tpl.render(**ctx)
    HTML(string=html_str, base_url=str(HERE)).write_pdf(out_path)
    return str(pathlib.Path(out_path).resolve())


if __name__ == "__main__":
    import sys, json
    with open(sys.argv[1]) as f:
        data = json.load(f)
    out = render_voucher(data, sys.argv[2])
    print("rendered:", out)

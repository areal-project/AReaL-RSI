"""Self-contained legends whose samples match the plotted line and marker styles."""

from __future__ import annotations

from html import escape


def legend(items: list[tuple[str, str, str, str]]) -> str:
    """Render (description, color, symbol, dash) entries; symbols: line, point, both, cross."""
    entries = []
    for description, color, symbol, dash in items:
        if symbol not in {"line", "point", "both", "cross"}:
            raise ValueError("Unknown legend symbol")
        color, dash = escape(color, quote=True), escape(dash, quote=True)
        glyph = []
        if symbol in {"line", "both"}:
            glyph.append(
                f'<line x1="3" x2="45" y1="10" y2="10" stroke="{color}" '
                f'stroke-width="2.5" stroke-dasharray="{dash}"/>'
            )
        if symbol in {"point", "both"}:
            glyph.append(f'<circle cx="24" cy="10" r="4" fill="{color}" stroke="white"/>')
        if symbol == "cross":
            glyph.append(
                f'<path d="M 19 5 L 29 15 M 19 15 L 29 5" stroke="{color}" '
                'stroke-width="3" fill="none"/>'
            )
        entries.append(
            '<span style="display:flex;align-items:center;gap:8px;max-width:100%">'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 20" aria-hidden="true" '
            'style="width:48px;height:20px;flex:0 0 48px;margin:0">'
            + "".join(glyph)
            + f"</svg><span>{escape(description)}</span></span>"
        )
    return (
        '<div class="chart-legend" role="group" aria-label="图例" '
        'style="display:flex;flex-wrap:wrap;gap:10px 22px;padding:12px 0;'
        'font-size:13px;line-height:1.6;color:#44536b">' + "".join(entries) + "</div>"
    )

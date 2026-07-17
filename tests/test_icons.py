"""d2v.icons（ノードアイコン・絵文字廃止）のテスト。"""

from __future__ import annotations

from d2v import icons


def test_icon_filename_maps_known_types_and_falls_back():
    assert icons.icon_filename("router") == "router.png"
    assert icons.icon_filename("load-balancer") == "load-balancer.png"
    # 未知・空・None は unknown へフォールバック
    assert icons.icon_filename("mystery") == "unknown.png"
    assert icons.icon_filename(None) == "unknown.png"
    # 大文字・前後空白も正規化
    assert icons.icon_filename("  Router ") == "router.png"


def test_html_label_embeds_icon_and_escapes_text():
    label = icons.html_label("firewall", ["fw-01", "10.0.0.1 <primary>"])
    assert label.startswith("<<TABLE") and label.endswith("TABLE>>")
    assert '<IMG SRC="firewall.png"' in label
    # HTML 特殊文字はエスケープされる
    assert "&lt;primary&gt;" in label
    assert "<primary>" not in label
    # 2 行目以降は小さめフォント
    assert '<FONT POINT-SIZE="8">' in label


def test_inject_icons_into_dot_converts_d2vtype_nodes():
    dot = (
        'digraph G {\n'
        '  "r1" [label="r1\\n1.1.1.1", d2vtype="router", fillcolor="#fff"];\n'
        '  "s1" [label="s1", d2vtype="switch"];\n'
        '  "plain" [label="no-icon"];\n'
        '  "r1" -> "s1";\n'
        '}'
    )
    out = icons.inject_icons_into_dot(dot)
    # d2vtype 属性は除去され、IMG が注入される
    assert "d2vtype" not in out
    assert out.count("<IMG") == 2
    assert '<IMG SRC="router.png"' in out
    assert '<IMG SRC="switch.png"' in out
    # アイコンを持たないノード・エッジは変更されない
    assert '"plain" [label="no-icon"]' in out
    assert '"r1" -> "s1";' in out
    # fillcolor 等の既存属性は保持される
    assert 'fillcolor="#fff"' in out


def test_inject_icons_is_noop_without_d2vtype():
    dot = 'digraph G { "a" [label="a"]; "a" -> "a"; }'
    assert icons.inject_icons_into_dot(dot) == dot


def test_inline_svg_icons_replaces_external_reference():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<image xlink:href="router.png" width="26px" height="26px" x="10" y="20"/>'
        '</svg>'
    )
    out = icons.inline_svg_icons(svg)
    assert "<image" not in out
    assert 'viewBox="0 0 96 96"' in out
    assert "router.png" not in out


def test_inline_svg_icons_keeps_unknown_image_refs():
    svg = '<image xlink:href="photo.png" width="10" height="10" x="0" y="0"/>'
    # d2v のアイコンでない画像参照はそのまま残す
    assert icons.inline_svg_icons(svg) == svg


def test_render_svg_and_png_for_all_device_types():
    from PIL import Image

    for t in icons.DEVICE_TYPES:
        svg = icons.render_svg(t)
        assert svg.startswith("<?xml") and "</svg>" in svg
        img = icons.render_png(t, size=64)
        assert isinstance(img, Image.Image)
        assert img.size == (64, 64)


def test_write_assets_creates_svg_and_png(tmp_path):
    out = icons.write_assets(tmp_path)
    for t in icons.DEVICE_TYPES:
        assert (out / f"{t}.svg").exists()
        assert (out / f"{t}.png").exists()

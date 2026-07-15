"""v2d 前処理と抽出器（LLM モック）のテスト。"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from d2v.v2d import extractor
from d2v.v2d.preprocess import ImagePreprocessError, load_and_preprocess


def _make_png(path, size=(1000, 600), color=(255, 255, 255)):
    Image.new("RGB", size, color).save(path)
    return path


def test_preprocess_data_url_and_size(tmp_path):
    p = _make_png(tmp_path / "diagram.png", size=(1000, 600))
    result = load_and_preprocess(p)
    assert result.original_size == (1000, 600)
    assert result.data_url.startswith("data:image/png;base64,")
    assert result.warnings == []


def test_preprocess_downscales_over_max_dim(tmp_path):
    p = _make_png(tmp_path / "big.png", size=(4000, 2000))
    result = load_and_preprocess(p, max_dim=1000)
    assert max(result.width, result.height) == 1000


def test_preprocess_warns_below_min_width(tmp_path):
    p = _make_png(tmp_path / "small.png", size=(400, 300))
    result = load_and_preprocess(p, min_width=800)
    assert result.warnings  # 推奨幅未満の警告


def test_preprocess_rejects_unsupported_suffix(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(ImagePreprocessError):
        load_and_preprocess(bad)


def test_preprocess_missing_file(tmp_path):
    with pytest.raises(ImagePreprocessError):
        load_and_preprocess(tmp_path / "nope.png")


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response

    def chat_with_images(self, system, user, image_data_urls):
        # 画像がデータ URL として渡っていることを軽く検証
        assert image_data_urls and image_data_urls[0].startswith("data:image/")
        return self._response


def test_extractor_parses_json_response(tmp_path, monkeypatch):
    p = _make_png(tmp_path / "diagram.png")
    canned = {
        "nodes": [
            {"id": "n1", "hostname": "r1", "device_type": "router", "confidence": 0.9},
            {"id": "n2", "hostname": "s1", "device_type": "switch", "confidence": 0.9},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "source_port": "Gi0/1",
             "target_port": "Gi0/0", "confidence": 0.9},
        ],
        "clusters": [],
        "notes": [],
        "confidence": 0.9,
    }
    response = f"```json\n{json.dumps(canned)}\n```"
    monkeypatch.setattr(extractor, "get_llm", lambda: _FakeLLM(response))

    diagram, pre = extractor.extract_from_image(p)
    assert len(diagram.nodes) == 2
    assert len(diagram.edges) == 1
    assert diagram.nodes[0].device_type == "router"
    assert pre.data_url.startswith("data:image/png;base64,")


def test_extractor_raises_on_non_json(tmp_path, monkeypatch):
    p = _make_png(tmp_path / "diagram.png")
    monkeypatch.setattr(extractor, "get_llm", lambda: _FakeLLM("すみません、解析できません。"))
    with pytest.raises(extractor.ExtractionError):
        extractor.extract_from_image(p)

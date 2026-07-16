"""Web GUI API のテスト（FastAPI TestClient・LLM をモックして高速化）。

service 層（LLM を使う `run_d2v_job` / `run_v2d_job`）を軽量なフェイクへ差し替え、
ジョブ作成・状態遷移・成果物取得・不正入力拒否といった API 層の挙動を検証する。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from d2v.evaluator import EvaluationResult, RuleCheckResult
from d2v.pipeline import IterationRecord, PipelineResult
from d2v.progress import ProgressEvent
from d2v.web import app as app_module
from d2v.web import jobs as jobs_module
from d2v.web import service
from d2v.web.service import D2VJobResult, DiagramOutput, V2DJobResult

client = TestClient(app_module.app)

_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c636000000000ffff03000006000557"
    "bfabd40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _clear_registry():
    """各テストの前後でジョブレジストリを空にする（相互干渉を防ぐ）。"""
    jobs_module.registry._jobs.clear()
    yield
    jobs_module.registry._jobs.clear()


def _wait_state(job_id: str, timeout: float = 5.0) -> dict:
    """ジョブが running を抜けるまで待って状態 dict を返す。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["state"] != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("ジョブがタイムアウトしました")


def _fake_d2v(image_name: str = "diagram_best.png"):
    """フェイクの run_d2v_job を返す（画像を書き出し D2VJobResult を返す）。"""
    def _run(params, progress=None):
        params.output_dir.mkdir(parents=True, exist_ok=True)
        img = params.output_dir / image_name
        img.write_bytes(_PNG_1x1)
        if progress:
            progress(ProgressEvent(stage="topology", message="解析"))
            progress(ProgressEvent(stage="score", score=9, passed=True, is_best=True))
        ev = EvaluationResult(
            iteration=0, score=9, passed=True, issues=["軽微な指摘"],
            rule_checks=RuleCheckResult(
                node_count_ok=True, edge_count_ok=True, has_taillabel=True,
                has_headlabel=True, has_subgraph_cluster=True, has_ip_labels=True,
            ),
        )
        rec = IterationRecord(iteration=0, dot_code="digraph{}", image_path=img,
                              result=ev, is_best=True)
        pr = PipelineResult(best_dot="digraph G {}", best_result=ev,
                            best_image=img, records=[rec])
        out = DiagramOutput(key="single", title="構成図", final_image=img, result=pr)
        return D2VJobResult(mode="single", outputs=[out],
                            output_dir=params.output_dir, topology_text="t")
    return _run


def _fake_v2d():
    """フェイクの run_v2d_job を返す（YAML/サイドカーを書き出す）。"""
    def _run(image_path, output_dir, truth_path=None, rerender=False, fmt="png", progress=None):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = output_dir / "out.yaml"
        yaml_path.write_text("network-model: {}\n", encoding="utf-8")
        sidecar = output_dir / "out.v2d.json"
        sidecar.write_text("{}", encoding="utf-8")
        if progress:
            progress(ProgressEvent(stage="v2d_extracted", extra={
                "nodes": 3, "edges": 2, "clusters": 1, "confidence": 0.9}))
        return V2DJobResult(
            yaml_text="network-model: {}\n", yaml_path=yaml_path,
            sidecar_path=sidecar, original_image=Path(image_path),
            node_count=3, edge_count=2, cluster_count=1, confidence=0.9,
            notes=["所見"], low_confidence_nodes=[],
        )
    return _run


# ---------------------------------------------------------------------------
# メタ・サンプル
# ---------------------------------------------------------------------------


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert "llm_provider" in body
    assert "defaults" in body
    assert set(body["formats"]) == {"png", "svg"}


def test_example_valid():
    r = client.get("/api/examples/sample_topology_small.yaml")
    assert r.status_code == 200
    assert "network-model" in r.json()["content"]


def test_example_path_traversal_rejected():
    r = client.get("/api/examples/..%2Fpyproject.toml")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# d2v ジョブ
# ---------------------------------------------------------------------------


def test_d2v_bad_source():
    r = client.post("/api/d2v/jobs", json={"source": "bogus"})
    assert r.status_code == 400


def test_d2v_missing_example():
    r = client.post("/api/d2v/jobs", json={"source": "example"})
    assert r.status_code == 400


def test_d2v_example_path_traversal():
    r = client.post("/api/d2v/jobs", json={"source": "example", "example": "../pyproject.toml"})
    assert r.status_code == 400


def test_d2v_oversized_yaml():
    big = "x" * 1_000_001
    r = client.post("/api/d2v/jobs", json={"source": "text", "yaml_text": big})
    assert r.status_code == 413


def test_d2v_lifecycle_and_artifacts(monkeypatch):
    monkeypatch.setattr(service, "run_d2v_job", _fake_d2v())
    r = client.post("/api/d2v/jobs", json={
        "source": "example", "example": "sample_topology_small.yaml",
        "max_iter": 1, "threshold": 1,
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    data = _wait_state(job_id)
    assert data["state"] == "succeeded"
    assert data["mode"] == "single"
    assert data["outputs"][0]["score"] == 9

    # 成果物取得
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 200
    dot = client.get(f"/api/jobs/{job_id}/dot")
    assert dot.status_code == 200 and "digraph" in dot.text
    ev = client.get(f"/api/jobs/{job_id}/eval").json()
    assert ev["score"] == 9 and ev["passed"] is True

    # 履歴一覧に載る
    listing = client.get("/api/jobs").json()["jobs"]
    assert any(j["id"] == job_id for j in listing)


def test_job_not_found():
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.get("/api/jobs/deadbeef/image").status_code == 404


# ---------------------------------------------------------------------------
# v2d ジョブ
# ---------------------------------------------------------------------------


def test_v2d_bad_extension():
    r = client.post("/api/v2d/jobs", files={"image": ("x.txt", b"abc", "text/plain")})
    assert r.status_code == 400


def test_v2d_bad_mime():
    r = client.post("/api/v2d/jobs", files={"image": ("x.png", b"abc", "text/plain")})
    assert r.status_code == 400


def test_v2d_empty_file():
    r = client.post("/api/v2d/jobs", files={"image": ("x.png", b"", "image/png")})
    assert r.status_code == 400


def test_v2d_lifecycle(monkeypatch):
    monkeypatch.setattr(service, "run_v2d_job", _fake_v2d())
    r = client.post("/api/v2d/jobs", files={"image": ("x.png", _PNG_1x1, "image/png")})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    data = _wait_state(job_id)
    assert data["state"] == "succeeded"
    assert data["v2d"]["node_count"] == 3

    yaml = client.get(f"/api/jobs/{job_id}/v2d/yaml")
    assert yaml.status_code == 200 and "network-model" in yaml.text
    assert client.get(f"/api/jobs/{job_id}/v2d/sidecar").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/v2d/original").status_code == 200
    # 再描画は未実施なので 404
    assert client.get(f"/api/jobs/{job_id}/v2d/rerender").status_code == 404


# ---------------------------------------------------------------------------
# セマンティック検証 API（/api/validate）— LLM 不要で同期実行
# ---------------------------------------------------------------------------


def test_validate_example_ok():
    r = client.post("/api/validate", json={
        "source": "example", "example": "sample_topology_small.yaml",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True           # 構造 error なし
    assert data["counts"]["error"] == 0
    assert data["explain_error"] is None
    # 非冗長ツリーのため SPOF/橋の warning がある
    assert data["counts"]["warning"] > 0
    rules = {i["rule"] for i in data["issues"]}
    assert "spof-device" in rules


def test_validate_text_with_errors():
    broken = (
        "network-model:\n"
        "  physical-layer:\n"
        "    device:\n"
        "      - device-id: a\n"
        "      - device-id: a\n"           # 重複
        "    physical-connection:\n"
        "      - connection-id: c1\n"
        "        endpoint:\n"
        "          - device-id: a\n"
        "            interface-id: g0\n"
        "          - device-id: ghost\n"   # 未定義参照
        "            interface-id: g0\n"
    )
    r = client.post("/api/validate", json={"source": "text", "yaml_text": broken})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["passed"] is False
    rules = {i["rule"] for i in data["issues"]}
    assert "duplicate-device-id" in rules
    assert "unknown-device-ref" in rules


def test_validate_strict_fails_on_warning():
    r = client.post("/api/validate", json={
        "source": "example", "example": "sample_topology_small.yaml",
        "strict": True,
    })
    data = r.json()
    assert data["ok"] is True          # error はない
    assert data["passed"] is False     # strict では warning で不合格


def test_validate_bad_yaml_returns_400():
    r = client.post("/api/validate", json={
        "source": "text", "yaml_text": "not-a-network-model: true\n",
    })
    assert r.status_code == 400


def test_validate_example_traversal_blocked():
    r = client.post("/api/validate", json={
        "source": "example", "example": "../pyproject.toml",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 意味的 diff API（/api/diff）
# ---------------------------------------------------------------------------

_BEFORE_YAML = (
    "network-model:\n"
    "  physical-layer:\n"
    "    device:\n"
    "      - device-id: r1\n"
    "        zone: core\n"
    "      - device-id: r2\n"
    "        zone: core\n"
    "    physical-connection:\n"
    "      - connection-id: c1\n"
    "        endpoint:\n"
    "          - device-id: r1\n"
    "            interface-id: g0\n"
    "          - device-id: r2\n"
    "            interface-id: g0\n"
)

_AFTER_YAML = (
    "network-model:\n"
    "  physical-layer:\n"
    "    device:\n"
    "      - device-id: r1\n"
    "        zone: core\n"
    "      - device-id: r3\n"
    "        zone: edge\n"
    "    physical-connection:\n"
    "      - connection-id: c2\n"
    "        endpoint:\n"
    "          - device-id: r1\n"
    "            interface-id: g0\n"
    "          - device-id: r3\n"
    "            interface-id: g0\n"
)


def test_diff_text_sources():
    r = client.post("/api/diff", json={
        "before": {"source": "text", "yaml_text": _BEFORE_YAML},
        "after": {"source": "text", "yaml_text": _AFTER_YAML},
        "image": False,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["is_empty"] is False
    assert data["diff"]["nodes_added"] == ["r3"]
    assert data["diff"]["nodes_removed"] == ["r2"]


def test_diff_identical_is_empty():
    r = client.post("/api/diff", json={
        "before": {"source": "text", "yaml_text": _BEFORE_YAML},
        "after": {"source": "text", "yaml_text": _BEFORE_YAML},
        "image": False,
    })
    data = r.json()
    assert data["is_empty"] is True
    assert data["image_token"] is None


def test_diff_generates_image_and_serves_it():
    r = client.post("/api/diff", json={
        "before": {"source": "text", "yaml_text": _BEFORE_YAML},
        "after": {"source": "text", "yaml_text": _AFTER_YAML},
        "image": True,
    })
    data = r.json()
    token = data["image_token"]
    if token is None:
        import pytest
        pytest.skip("Graphviz 未インストールで画像が生成されなかった")
    img = client.get(f"/api/diff/image/{token}")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")


def test_diff_image_unknown_token_404():
    assert client.get("/api/diff/image/deadbeef").status_code == 404


def test_diff_bad_yaml_returns_400():
    r = client.post("/api/diff", json={
        "before": {"source": "text", "yaml_text": "nope: true\n"},
        "after": {"source": "text", "yaml_text": _AFTER_YAML},
        "image": False,
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# focus ライブプレビュー API（edit-assist）
# ---------------------------------------------------------------------------


def test_focus_resolve_device_and_connection():
    text = Path("examples/sample_topology_small.yaml").read_text(encoding="utf-8")
    lines = text.splitlines()

    # device ブロック内のカーソル → その 1 台
    dev_line = next(
        i + 1 for i, ln in enumerate(lines) if 'device-id: "fw-01"' in ln
    )
    r = client.post(
        "/api/focus/resolve",
        json={"source": "text", "yaml_text": text, "line": dev_line + 2},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["focus"] == ["fw-01"]
    assert data["context"] == "device"
    # device_lines に定義行が入り、双方向ジャンプに使える
    assert data["device_lines"]["fw-01"] >= 1

    # physical-connection ブロック内のカーソル → 両端 2 台
    conn_line = next(
        i + 1 for i, ln in enumerate(lines)
        if "router-01__fw-01" in ln
    )
    r = client.post(
        "/api/focus/resolve",
        json={"source": "text", "yaml_text": text, "line": conn_line + 1},
    )
    data = r.json()
    assert data["context"] == "connection"
    assert set(data["focus"]) == {"router-01", "fw-01"}


def test_focus_preview_by_line_returns_svg():
    text = Path("examples/sample_topology_small.yaml").read_text(encoding="utf-8")
    line = next(
        i + 1 for i, ln in enumerate(text.splitlines())
        if 'device-id: "fw-01"' in ln
    )
    r = client.post(
        "/api/focus/preview",
        json={"source": "text", "yaml_text": text, "line": line, "hops": 1},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["focus"] == ["fw-01"]
    if data["svg"] is None:
        import pytest
        pytest.skip("Graphviz 未インストールで SVG が生成されなかった")
    assert "<svg" in data["svg"]
    # 双方向ジャンプ用の id が SVG に埋まっている
    assert "device:fw" in data["svg"]


def test_focus_preview_explicit_focus():
    r = client.post(
        "/api/focus/preview",
        json={"source": "example", "example": "sample_topology_small.yaml",
              "focus": ["core-sw-01"], "hops": 2},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["focus"] == ["core-sw-01"]
    assert data["context"] == "explicit"


def test_focus_preview_unknown_device_is_200_with_not_found():
    r = client.post(
        "/api/focus/preview",
        json={"source": "example", "example": "sample_topology_small.yaml",
              "focus": ["no-such-node"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["svg"] is None
    assert data["not_found"] == ["no-such-node"]


def test_focus_preview_no_focus_is_200_with_message():
    # 注目ノードが特定できない（line も focus も無い）場合も 200
    r = client.post(
        "/api/focus/preview",
        json={"source": "example", "example": "sample_topology_small.yaml"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["svg"] is None
    assert data["focus"] == []
    assert data["message"]


def test_focus_preview_bad_yaml_returns_400():
    r = client.post(
        "/api/focus/preview",
        json={"source": "text", "yaml_text": "nope: true\n", "focus": ["x"]},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# design lint diagnostics API（edit-assist）
# ---------------------------------------------------------------------------


def test_lint_returns_issues_with_lines():
    # web-server が単一リンク等の指摘が出るサンプル。issue が行番号付きで返る。
    r = client.post(
        "/api/lint",
        json={"source": "example", "example": "sample_topology_small.yaml"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "counts" in data and "issues" in data
    for iss in data["issues"]:
        assert "rule" in iss and "severity" in iss and "message" in iss
        # line は int か null。int の場合は 1 以上。
        assert iss["line"] is None or iss["line"] >= 1


def test_lint_maps_target_to_device_line():
    # 宙ぶらりん接続を作って、issue が該当 device/connection の行を指すことを確認
    text = Path("examples/sample_topology_small.yaml").read_text(encoding="utf-8")
    # 存在しない device を参照する接続を追加（dangling）
    text += (
        "\n      - connection-id: \"broken-link\"\n"
        "        endpoint:\n"
        "          - device-id: \"ghost-01\"\n"
        "            interface-id: \"eth9\"\n"
        "          - device-id: \"pc-01\"\n"
        "            interface-id: \"eth9\"\n"
    )
    r = client.post("/api/lint", json={"source": "text", "yaml_text": text})
    assert r.status_code == 200
    data = r.json()
    # 何らかの error/warning が検出される
    assert data["counts"].get("error", 0) + data["counts"].get("warning", 0) >= 1


def test_lint_bad_yaml_returns_400():
    r = client.post("/api/lint", json={"source": "text", "yaml_text": "nope: true\n"})
    assert r.status_code == 400

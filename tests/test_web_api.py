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

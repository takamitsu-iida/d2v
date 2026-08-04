"""audit パイプライン: 設計 YAML + コンフィグファイル群 → AuditReport。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from d2v.audit.comparator import compare
from d2v.audit.extractor import ExtractionError, extract_from_config
from d2v.audit.schema import AuditIssue, AuditReport, ExtractedConfig
from d2v.errors import InputError
from d2v.parser import TopologyModel, load_model

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    """audit パイプラインの実行結果。"""

    report: AuditReport
    design_path: Path
    config_paths: list[Path]
    extraction_errors: dict[str, str] = field(default_factory=dict)  # device_id → エラーメッセージ


def _collect_config_paths(
    config_files: list[Path] | None,
    config_dir: Path | None,
) -> list[Path]:
    """--config / --config-dir の引数からコンフィグファイル一覧を構築する。"""
    if config_dir is not None:
        if not config_dir.is_dir():
            raise InputError(f"--config-dir に指定したパスがディレクトリではありません: {config_dir}")
        paths = sorted(config_dir.glob("*.txt"))
        if not paths:
            raise InputError(f"--config-dir にテキストファイル（*.txt）が見つかりません: {config_dir}")
        return paths

    if config_files:
        missing = [p for p in config_files if not p.exists()]
        if missing:
            raise InputError(f"コンフィグファイルが見つかりません: {', '.join(str(p) for p in missing)}")
        return list(config_files)

    raise InputError("--config または --config-dir のいずれかを指定してください。")


def run(
    design_path: Path,
    config_files: list[Path] | None = None,
    config_dir: Path | None = None,
) -> AuditResult:
    """設計 YAML とコンフィグファイル群を比較して AuditResult を返す。

    Args:
        design_path: iida-network-model YAML のパス。
        config_files: コンフィグファイルのリスト（stem が device-id に対応）。
        config_dir: コンフィグファイルを *.txt で一括探索するディレクトリ。

    Returns:
        AuditResult

    Raises:
        InputError: ファイルが見つからない場合など。
    """
    model: TopologyModel = load_model(design_path)
    paths = _collect_config_paths(config_files, config_dir)

    configs: list[ExtractedConfig] = []
    extraction_errors: dict[str, str] = {}
    issues_from_errors: list[AuditIssue] = []

    for path in paths:
        device_id = path.stem
        try:
            config_text = path.read_text(encoding="utf-8")
            cfg = extract_from_config(config_text, device_id)
            configs.append(cfg)
            logger.info("抽出完了: %s (vendor=%s, confidence=%.2f)", device_id, cfg.vendor, cfg.confidence)
        except ExtractionError as e:
            logger.warning("抽出失敗: %s — %s", device_id, e)
            extraction_errors[device_id] = str(e)
            issues_from_errors.append(AuditIssue(
                rule="extraction-failed",
                severity="error",
                device_id=device_id,
                message="コンフィグの LLM 抽出に失敗したためこのデバイスの検査をスキップ",
                detail=str(e),
            ))

    report = compare(model, configs)
    # 抽出エラー分を先頭に追加
    if issues_from_errors:
        all_issues = issues_from_errors + report.issues
        report = AuditReport.from_issues(all_issues)

    return AuditResult(
        report=report,
        design_path=design_path,
        config_paths=paths,
        extraction_errors=extraction_errors,
    )

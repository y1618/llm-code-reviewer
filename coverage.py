from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


@dataclass
class CoverageTarget:
    path: str
    sha256: str
    start_line: int
    end_line: int
    chunk_id: str
    reason: Optional[str] = None

    def key(self) -> str:
        return f"{self.path}:{self.start_line}:{self.end_line}:{self.sha256}:{self.chunk_id}"


@dataclass
class LedgerRecord:
    commit: Optional[str]
    files: List[Dict[str, object]]
    model: str
    api_url: str
    max_context: int
    prompt_hash: str
    tokens: Dict[str, int]
    status: str
    error_message: Optional[str]
    ts: str


@dataclass
class CoverageLedger:
    code_dir: Path
    commit_sha: Optional[str]
    ledger_filename: str = "ledger.jsonl"
    report_json_filename: str = "report.json"
    report_md_filename: str = "report.md"
    badge_filename: str = "badge.json"
    targets: Dict[str, CoverageTarget] = field(default_factory=dict)
    covered: Set[str] = field(default_factory=set)
    records: List[LedgerRecord] = field(default_factory=list)
    reasons: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.coverage_dir = self.code_dir / "coverage"
        self.coverage_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.coverage_dir / self.ledger_filename
        self.report_json_path = self.coverage_dir / self.report_json_filename
        self.report_md_path = self.coverage_dir / self.report_md_filename
        self.badge_path = self.coverage_dir / self.badge_filename
        self._load_existing_records()

    def _load_existing_records(self) -> None:
        if not self.ledger_path.exists():
            return

        with self.ledger_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("commit") != self.commit_sha:
                    continue

                record = LedgerRecord(
                    commit=data.get("commit"),
                    files=data.get("files", []),
                    model=data.get("model", ""),
                    api_url=data.get("api_url", ""),
                    max_context=data.get("max_context", 0),
                    prompt_hash=data.get("prompt_hash", ""),
                    tokens=data.get("tokens", {}),
                    status=data.get("status", "error"),
                    error_message=data.get("error_message"),
                    ts=data.get("ts", ""),
                )
                self.records.append(record)

                if record.status == "ok":
                    for file_entry in record.files:
                        key = self._target_key_from_entry(file_entry)
                        if key:
                            self.covered.add(key)

    def _target_key_from_entry(self, entry: Dict[str, object]) -> Optional[str]:
        path = entry.get("path")
        sha = entry.get("sha256")
        start = entry.get("start_line")
        end = entry.get("end_line")
        chunk_id = entry.get("chunk_id")
        if not isinstance(path, str) or not isinstance(sha, str):
            return None
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if not isinstance(chunk_id, str):
            return None
        return f"{path}:{start}:{end}:{sha}:{chunk_id}"

    def register_targets(self, targets: Iterable[CoverageTarget]) -> None:
        for target in targets:
            self.targets[target.key()] = target
            if target.reason:
                self.reasons[target.key()] = target.reason

    def record_skip(self, path: str, reason: str) -> None:
        """Register a skipped path so coverage reports can surface the reason."""

        reason_text = reason.strip() or "skipped"
        placeholder_hash = hashlib.sha256(f"{path}:{reason_text}".encode("utf-8")).hexdigest()
        chunk_id = f"{path}#skip@{placeholder_hash[:8]}"
        target = CoverageTarget(
            path=path,
            sha256=placeholder_hash,
            start_line=1,
            end_line=1,
            chunk_id=chunk_id,
            reason=reason_text,
        )
        if target.key() not in self.targets:
            self.targets[target.key()] = target
            self.reasons[target.key()] = reason_text

    def append_record(
        self,
        *,
        files: List[Dict[str, object]],
        model: str,
        api_url: str,
        max_context: int,
        prompt_hash: str,
        prompt_tokens: int,
        completion_tokens: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        record = LedgerRecord(
            commit=self.commit_sha,
            files=files,
            model=model,
            api_url=api_url,
            max_context=max_context,
            prompt_hash=prompt_hash,
            tokens={
                "prompt_est": prompt_tokens,
                "completion_est": completion_tokens,
            },
            status=status,
            error_message=error_message,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, ensure_ascii=False))
            handle.write("\n")

        self.records.append(record)

        if status == "ok":
            for file_entry in files:
                key = self._target_key_from_entry(file_entry)
                if key:
                    self.covered.add(key)

    def build_report(self, review_results: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
        total_segments = len(self.targets)
        covered_segments = len(self.covered & set(self.targets.keys()))
        missed_keys = set(self.targets.keys()) - self.covered
        missed_segments = len(missed_keys)

        per_file: Dict[str, Dict[str, int]] = {}
        for key, target in self.targets.items():
            file_stats = per_file.setdefault(target.path, {"total": 0, "covered": 0})
            file_stats["total"] += 1
            if key in self.covered:
                file_stats["covered"] += 1

        per_dir: Dict[str, Dict[str, int]] = {}
        for path, stats in per_file.items():
            directory = str(Path(path).parent)
            dir_stats = per_dir.setdefault(directory, {"total": 0, "covered": 0})
            dir_stats["total"] += stats["total"]
            dir_stats["covered"] += stats["covered"]

        missed_details = []
        for key in sorted(missed_keys):
            target = self.targets[key]
            data = target.__dict__.copy()
            if reason := self.reasons.get(key):
                data["reason"] = reason
            missed_details.append(data)

        reviewed_segments: Dict[str, Dict[str, str]] = {}
        for record in self.records:
            if record.status != "ok":
                continue
            for entry in record.files:
                path = entry.get("path")
                if not isinstance(path, str):
                    continue
                reviewed_segments[path] = {
                    "chunk_id": str(entry.get("chunk_id", "")),
                    "model": record.model,
                    "ts": record.ts,
                }

        reviewed_details = [
            {"path": path, **info}
            for path, info in sorted(reviewed_segments.items(), key=lambda item: item[0])
        ]

        severity_histogram: Dict[str, int] = {}
        risk_histogram: Dict[str, int] = {str(i): 0 for i in range(1, 11)}
        issue_hotspots: Dict[str, int] = {}

        review_results = review_results or []
        for result in review_results:
            path = result.get("file")
            reviews = result.get("reviews", [])  # type: ignore[assignment]
            if not isinstance(reviews, list):
                continue
            directory = "."
            if isinstance(path, str):
                directory = str(Path(path).parent) or "."
                issue_hotspots[directory] = issue_hotspots.get(directory, 0) + len(reviews)
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                severity = str(review.get("severity", "")).lower()
                if severity:
                    severity_histogram[severity] = severity_histogram.get(severity, 0) + 1
                risk_value = review.get("risk_score")
                try:
                    risk_int = int(risk_value)
                except (TypeError, ValueError):
                    continue
                if 1 <= risk_int <= 10:
                    risk_histogram[str(risk_int)] += 1
                else:
                    risk_histogram.setdefault("other", 0)
                    risk_histogram["other"] += 1

        issue_hotspots_sorted = [
            {"directory": directory, "issues": count}
            for directory, count in sorted(
                issue_hotspots.items(), key=lambda item: (-item[1], item[0])
            )
        ]

        report = {
            "commit": self.commit_sha,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_segments": total_segments,
            "covered_segments": covered_segments,
            "missed_segments": missed_segments,
            "coverage_ratio": (covered_segments / total_segments) if total_segments else 1.0,
            "files": {
                path: {
                    "total_segments": stats["total"],
                    "covered_segments": stats["covered"],
                    "coverage_ratio": (
                        stats["covered"] / stats["total"] if stats["total"] else 1.0
                    ),
                }
                for path, stats in sorted(per_file.items())
            },
            "directories": {
                directory: {
                    "total_segments": stats["total"],
                    "covered_segments": stats["covered"],
                    "coverage_ratio": (
                        stats["covered"] / stats["total"] if stats["total"] else 1.0
                    ),
                }
                for directory, stats in sorted(per_dir.items())
            },
            "missed": missed_details,
            "reviewed": reviewed_details,
            "severity_histogram": severity_histogram,
            "risk_histogram": risk_histogram,
            "issue_hotspots": issue_hotspots_sorted,
        }

        with self.report_json_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)

        self._write_markdown_report(report)
        self._write_badge(missed_segments == 0)
        return report

    def _write_markdown_report(self, report: Dict[str, object]) -> None:
        lines: List[str] = []
        lines.append(f"# Coverage Report (commit: {report.get('commit')})")
        lines.append("")
        lines.append(f"- Timestamp: {report.get('timestamp')}")
        lines.append(f"- Segments covered: {report.get('covered_segments')} / {report.get('total_segments')}")
        ratio = report.get("coverage_ratio", 0.0)
        lines.append(f"- Coverage ratio: {ratio:.2%}")
        lines.append("")

        lines.append("## Directory coverage")
        lines.append("")
        lines.append("| Directory | Segments | Covered | Coverage |")
        lines.append("| --- | ---: | ---: | ---: |")
        directories: Dict[str, Dict[str, float]] = report.get("directories", {})  # type: ignore
        for directory, stats in directories.items():
            lines.append(
                f"| {directory or '.'} | {stats['total_segments']} | {stats['covered_segments']} | {stats['coverage_ratio']:.2%} |"
            )
        lines.append("")

        lines.append("## File coverage")
        lines.append("")
        lines.append("| File | Segments | Covered | Coverage |")
        lines.append("| --- | ---: | ---: | ---: |")
        files: Dict[str, Dict[str, float]] = report.get("files", {})  # type: ignore
        for path, stats in files.items():
            lines.append(
                f"| {path} | {stats['total_segments']} | {stats['covered_segments']} | {stats['coverage_ratio']:.2%} |"
            )
        lines.append("")

        lines.append("## 未レビューセグメント")
        lines.append("")
        lines.append("| File | Lines | Chunk | Reason |")
        lines.append("| --- | --- | --- | --- |")
        missed: List[Dict[str, object]] = report.get("missed", [])  # type: ignore
        if not missed:
            lines.append("| (none) | - | - | - |")
        else:
            for item in missed:
                lines.append(
                    "| {path} | {lines} | {chunk} | {reason} |".format(
                        path=item.get("path", ""),
                        lines=f"{item.get('start_line', '-')}-{item.get('end_line', '-')}",
                        chunk=item.get("chunk_id", ""),
                        reason=item.get("reason", ""),
                    )
                )
        lines.append("")

        lines.append("## レビュー済みセグメント")
        lines.append("")
        lines.append("| File | Chunk | Model | Timestamp |")
        lines.append("| --- | --- | --- | --- |")
        reviewed: List[Dict[str, object]] = report.get("reviewed", [])  # type: ignore
        if not reviewed:
            lines.append("| (none) | - | - | - |")
        else:
            for item in reviewed:
                lines.append(
                    "| {path} | {chunk} | {model} | {ts} |".format(
                        path=item.get("path", ""),
                        chunk=item.get("chunk_id", ""),
                        model=item.get("model", ""),
                        ts=item.get("ts", ""),
                    )
                )
        lines.append("")

        lines.append("## 指摘の多いディレクトリ Top")
        lines.append("")
        lines.append("| Directory | Issues |")
        lines.append("| --- | ---: |")
        hotspots: List[Dict[str, object]] = report.get("issue_hotspots", [])  # type: ignore
        if not hotspots:
            lines.append("| (none) | 0 |")
        else:
            for item in hotspots[:10]:
                lines.append(
                    "| {directory} | {issues} |".format(
                        directory=item.get("directory", ""),
                        issues=item.get("issues", 0),
                    )
                )
        lines.append("")

        lines.append("## 重大度別ヒストグラム")
        lines.append("")
        lines.append("| Severity | Findings |")
        lines.append("| --- | ---: |")
        severity_histogram: Dict[str, int] = report.get("severity_histogram", {})  # type: ignore
        non_zero_severity = {k: v for k, v in severity_histogram.items() if v}
        if not non_zero_severity:
            lines.append("| (none) | 0 |")
        else:
            for severity, count in sorted(non_zero_severity.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"| {severity} | {count} |")
        lines.append("")

        lines.append("## リスクスコア分布")
        lines.append("")
        lines.append("| Risk | Findings |")
        lines.append("| --- | ---: |")
        risk_histogram: Dict[str, int] = report.get("risk_histogram", {})  # type: ignore
        non_zero_risk = {k: v for k, v in risk_histogram.items() if v}
        if not non_zero_risk:
            lines.append("| (none) | 0 |")
        else:
            for risk, count in sorted(
                non_zero_risk.items(),
                key=lambda item: (int(item[0]) if item[0].isdigit() else 100, item[0]),
            ):
                lines.append(f"| {risk} | {count} |")
        lines.append("")

        with self.report_md_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def _write_badge(self, passed: bool) -> None:
        badge = {
            "label": "coverage",
            "message": "pass" if passed else "miss",
            "status": "pass" if passed else "fail",
        }
        with self.badge_path.open("w", encoding="utf-8") as handle:
            json.dump(badge, handle, ensure_ascii=False)

"""解包报告导出：JSON / CSV / 纯文本摘要。"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import ExtractResult, ExtractStatus
from .utils import human_size


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def summary_lines(results: Iterable[ExtractResult]) -> list[str]:
    results = list(results)
    ok = sum(1 for r in results if r.status is ExtractStatus.SUCCESS)
    need_pwd = [r for r in results if r.status is ExtractStatus.NEED_PASSWORD]
    failed = [r for r in results if r.status in (ExtractStatus.FAILED, ExtractStatus.UNSUPPORTED)]
    dry = [r for r in results if r.status is ExtractStatus.DRY_RUN]
    total_files = sum(r.file_count for r in results)
    total_bytes = sum(r.bytes_written for r in results)
    skipped = sum(r.skipped for r in results)

    lines = [
        f"解包报告  {_timestamp()}",
        "=" * 60,
        f"总计 {len(results)} 个文件 | 成功 {ok} | 需密码 {len(need_pwd)} | "
        f"失败/不支持 {len(failed)}" + (f" | 试运行 {len(dry)}" if dry else ""),
        f"累计解出 {total_files} 个文件 / {human_size(total_bytes)}"
        + (f"（按冲突策略跳过 {skipped} 个）" if skipped else ""),
        "-" * 60,
    ]
    for r in results:
        lines.append(f"[{r.status.icon} {r.status.label}] {r.source.name}  ({r.kind.label})")
        lines.append(f"        {r.message}")
        if r.output_dir:
            lines.append(f"        -> {r.output_dir}")
        if r.password:
            lines.append(f"        使用密码：{r.password}")
        if r.bytes_written:
            lines.append(f"        解出体积：{human_size(r.bytes_written)}"
                         f" | 耗时 {r.duration:.2f}s")
    return lines


def to_text(results: Iterable[ExtractResult]) -> str:
    return "\n".join(summary_lines(results))


def to_json(results: Iterable[ExtractResult], *, indent: int | None = 2) -> str:
    """把结果序列化为 JSON 文本（供 ``--json`` 直接打到标准输出）。"""
    data = {
        "generated_at": _timestamp(),
        "results": [r.to_dict() for r in results],
    }
    return json.dumps(data, ensure_ascii=False, indent=indent)


def export_json(results: Iterable[ExtractResult], dest: Path) -> Path:
    Path(dest).write_text(to_json(results), encoding="utf-8")
    return Path(dest)


def export_csv(results: Iterable[ExtractResult], dest: Path) -> Path:
    with open(dest, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["文件名", "路径", "类型", "状态", "解包器",
                         "文件数", "解出体积(字节)", "跳过数", "输出目录",
                         "耗时(秒)", "密码", "说明"])
        for r in results:
            writer.writerow([
                r.source.name, str(r.source), r.kind.label, r.status.label,
                r.extractor, r.file_count, r.bytes_written, r.skipped,
                str(r.output_dir) if r.output_dir else "",
                f"{r.duration:.3f}", r.password or "", r.message,
            ])
    return Path(dest)


def export(results: Iterable[ExtractResult], dest: str | Path) -> Path:
    """按扩展名自动选择导出格式。"""
    dest = Path(dest)
    suffix = dest.suffix.lower()
    results = list(results)
    if suffix == ".json":
        return export_json(results, dest)
    if suffix == ".csv":
        return export_csv(results, dest)
    dest.write_text(to_text(results), encoding="utf-8")
    return dest


def human_total(results: Iterable[ExtractResult]) -> str:
    return human_size(sum(r.bytes_written for r in results))

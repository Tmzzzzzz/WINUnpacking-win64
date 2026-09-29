"""命令行入口测试：探测、试运行、JSON 输出、退出码与参数兼容。"""
from __future__ import annotations

import json

import pytest
import samples

from unpacker import cli


def run(args: list[str]) -> int:
    return cli.run_cli(cli.build_parser().parse_args(args))


def test_inspect_only(workdir, capsys):
    zip_path = samples.write_zip(workdir / "a.zip", [("x.txt", b"1")])
    code = run(["-i", str(zip_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "ZIP 压缩包" in out
    assert not (workdir / "a.zip_unpacked").exists()


def test_extract_and_export_report(workdir, capsys):
    zip_path = samples.write_zip(workdir / "a.zip", [("d/x.txt", b"hi")])
    report = workdir / "r.csv"
    out_dir = workdir / "out"

    code = run([str(zip_path), "-o", str(out_dir), "--merge", "--report", str(report)])

    assert code == 0
    assert (out_dir / "d" / "x.txt").read_bytes() == b"hi"
    text = report.read_text(encoding="utf-8-sig")
    assert "成功" in text and "解出体积(字节)" in text


def test_dry_run_exit_code_and_no_write(workdir, capsys):
    zip_path = samples.write_zip(workdir / "a.zip", [("x.txt", b"1")])
    out_dir = workdir / "out"

    code = run([str(zip_path), "-o", str(out_dir), "--merge", "--dry-run"])

    assert code == 0
    assert not (out_dir / "x.txt").exists()
    assert "试运行" in capsys.readouterr().out


def test_json_output_is_pure_json(workdir, capsys):
    zip_path = samples.write_zip(workdir / "a.zip", [("x.txt", b"12345")])
    out_dir = workdir / "out"

    code = run([str(zip_path), "-o", str(out_dir), "--merge", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)      # 必须能直接解析
    assert payload["results"][0]["status"] == "success"
    assert payload["results"][0]["bytes_written"] == 5
    assert payload["results"][0]["file_count"] == 1


def test_overwrite_flag_is_conflict_alias(workdir):
    out_dir = workdir / "out"
    out_dir.mkdir()
    (out_dir / "f.txt").write_bytes(b"old")
    zip_path = samples.write_zip(workdir / "c.zip", [("f.txt", b"new")])

    code = run([str(zip_path), "-o", str(out_dir), "--merge", "--overwrite"])

    assert code == 0
    assert (out_dir / "f.txt").read_bytes() == b"new"


def test_conflict_option_skip(workdir):
    out_dir = workdir / "out"
    out_dir.mkdir()
    (out_dir / "f.txt").write_bytes(b"old")
    zip_path = samples.write_zip(workdir / "c.zip", [("f.txt", b"new")])

    run([str(zip_path), "-o", str(out_dir), "--merge", "--conflict", "skip"])

    assert (out_dir / "f.txt").read_bytes() == b"old"
    assert not (out_dir / "f_1.txt").exists()


def test_need_password_exit_code(workdir):
    zip_path = samples.write_zip(workdir / "enc.zip", [("a.txt", b"A")],
                                 password="right")
    code = run([str(zip_path), "-o", str(workdir / "out"), "--merge",
                "-p", "wrong"])
    assert code == 3


def test_no_targets_exit_code(workdir, capsys):
    code = run([str(workdir / "not-exists.zip")])
    assert code == 2
    assert "不存在" in capsys.readouterr().err


def test_encoding_option_is_applied(workdir):
    zip_path = samples.write_zip(workdir / "gbk.zip", [("中文.txt", b"x")],
                                 gbk_names=True)
    out_dir = workdir / "out"

    assert run([str(zip_path), "-o", str(out_dir), "--merge"]) == 0
    assert (out_dir / "中文.txt").exists()


@pytest.mark.parametrize("flag", ["--json", "--dry-run", "--conflict", "--encoding"])
def test_new_flags_are_registered(flag):
    assert flag.lstrip("-").replace("-", "_") in {
        a.dest for a in cli.build_parser()._actions
    }

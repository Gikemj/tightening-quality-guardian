from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _status(label: str, ok: bool, detail: str) -> None:
    mark = "通过" if ok else "需要处理"
    print(f"[{mark}] {label}：{detail}")


def main() -> int:
    """Read-only diagnosis for the local Conda/VS Code setup."""

    failures: list[str] = []
    print("质控前哨｜本地环境自检（不会联网，也不会修改文件）")
    print(f"Python 解释器：{sys.executable}")
    print(f"Python 版本：{sys.version.split()[0]}")
    print(f"项目目录：{PROJECT_ROOT}")

    python_supported = sys.version_info >= (3, 10)
    _status(
        "Python 兼容性",
        python_supported,
        "满足项目要求（>=3.10）" if python_supported else "请切换到 huawei 的 Python 3.12",
    )
    if not python_supported:
        failures.append("Python 版本过低")
    elif sys.version_info[:2] != (3, 12):
        print("[提示] 当前版本可以运行项目；为了与推荐环境一致，建议在 VS Code 选择 huawei Python 3.12。")

    expected_files = (
        "pyproject.toml",
        "data/tightening_events_demo.csv",
        "knowledge/ontology.json",
        "docs/index.html",
    )
    missing = [name for name in expected_files if not (PROJECT_ROOT / name).is_file()]
    files_ok = not missing
    _status("项目文件", files_ok, "关键文件齐全" if files_ok else f"缺少：{', '.join(missing)}")
    if missing:
        failures.append("项目文件不完整")

    package_found = importlib.util.find_spec("torque_guard") is not None
    _status(
        "Python 项目包",
        package_found,
        "当前解释器可以找到 torque_guard"
        if package_found
        else "当前解释器找不到 torque_guard；请执行 python -m pip install -e .",
    )
    if not package_found:
        failures.append("项目尚未安装到当前解释器")

    node_path = shutil.which("node")
    if node_path:
        completed = subprocess.run(
            [node_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        node_version = completed.stdout.strip() or completed.stderr.strip() or "版本未知"
        version_match = re.fullmatch(r"v(\d+)(?:\.\d+){1,2}", node_version)
        node_supported = (
            completed.returncode == 0
            and version_match is not None
            and int(version_match.group(1)) >= 20
        )
        detail = (
            f"{node_version}（{node_path}，满足 >=20）"
            if node_supported
            else f"{node_version}（项目网页测试要求 Node.js >=20）"
        )
        _status("Node.js 测试工具", node_supported, detail)
        if not node_supported:
            failures.append("Node.js 缺失、无法执行或版本低于 20")
    else:
        _status("Node.js 测试工具", False, "未找到 node；项目网页测试要求 Node.js >=20")
        failures.append("未安装 Node.js >=20")

    if failures:
        print("\n自检未通过：" + "；".join(failures))
        return 1

    print("\n本地开发环境已就绪。下一步可按 F5 调试，或运行 VS Code 默认测试任务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

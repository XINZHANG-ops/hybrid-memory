#!/usr/bin/env python3
"""
Hybrid Memory 一键安装脚本
- 创建 Python 虚拟环境
- 安装依赖
- 配置 Claude Code hooks
- 创建数据目录
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_ROOT / ".venv"


def get_claude_settings_path() -> Path:
    """获取 Claude 配置文件路径"""
    return Path.home() / ".claude" / "settings.json"


def get_python_executable() -> Path:
    """获取虚拟环境中的 Python 可执行文件路径"""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    else:
        return VENV_DIR / "bin" / "python"


def create_venv():
    """创建 Python 虚拟环境"""
    print("\n[1/4] Creating Python virtual environment...")

    if VENV_DIR.exists():
        print(f"Virtual environment already exists: {VENV_DIR}")
        response = input("Recreate? (y/N): ").strip().lower()
        if response != "y":
            print("Skipping venv creation.")
            return True
        import shutil
        shutil.rmtree(VENV_DIR)

    result = subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print("ERROR: Failed to create virtual environment")
        return False

    print(f"Virtual environment created: {VENV_DIR}")
    return True


def install_dependencies():
    """安装 Python 依赖"""
    print("\n[2/4] Installing Python dependencies...")

    python_exe = get_python_executable()
    if not python_exe.exists():
        print(f"ERROR: Python executable not found: {python_exe}")
        return False

    # 升级 pip
    subprocess.run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], cwd=PROJECT_ROOT)

    # 安装项目依赖
    result = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "-e", "."],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print("ERROR: Failed to install dependencies")
        return False

    print("Dependencies installed successfully!")
    return True


def setup_data_dir():
    """创建数据目录"""
    print("\n[3/4] Setting up data directories...")
    data_dir = PROJECT_ROOT / "data"
    projects_dir = data_dir / "projects"
    data_dir.mkdir(exist_ok=True)
    projects_dir.mkdir(exist_ok=True)
    print(f"Data directory: {data_dir}")
    return True


def configure_hooks():
    """配置 Claude Code hooks"""
    print("\n[4/4] Configuring Claude Code hooks...")

    settings_path = get_claude_settings_path()
    hooks_dir = PROJECT_ROOT / ".claude" / "hooks"
    python_exe = get_python_executable()

    if not hooks_dir.exists():
        print(f"ERROR: Hooks directory not found: {hooks_dir}")
        return False

    # 在 Mac/Linux 上设置 hook 脚本执行权限
    if sys.platform != "win32":
        import stat
        for py_file in hooks_dir.glob("*.py"):
            current_mode = py_file.stat().st_mode
            py_file.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Set execute permissions for hook scripts")

    # 读取现有配置
    settings = {}
    if settings_path.exists():
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        print(f"Found existing settings: {settings_path}")
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating new settings file: {settings_path}")

    # 构建 hooks 配置（使用虚拟环境的 Python）
    hooks_config = {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'"{python_exe}" "{hooks_dir / "sessionStart.py"}"',
                        "timeout": 30000,
                        "statusMessage": "Loading memory context...",
                    }
                ]
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'"{python_exe}" "{hooks_dir / "userPromptSubmit.py"}"',
                        "timeout": 5000,
                    }
                ]
            }
        ],
        "PermissionRequest": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'"{python_exe}" "{hooks_dir / "permissionRequest.py"}"',
                        "timeout": 5000,
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'"{python_exe}" "{hooks_dir / "postToolUse.py"}"',
                        "timeout": 5000,
                    }
                ]
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'"{python_exe}" "{hooks_dir / "stop.py"}"',
                        "timeout": 120000,
                    }
                ]
            }
        ],
    }

    # 合并 hooks 配置（保留其他 hooks）
    existing_hooks = settings.get("hooks", {})
    for hook_name, hook_config in hooks_config.items():
        existing_hooks[hook_name] = hook_config

    settings["hooks"] = existing_hooks

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    print(f"Hooks configured in: {settings_path}")
    print("Hooks registered:")
    print("  - SessionStart: Load memory context on new session")
    print("  - UserPromptSubmit: Record user messages")
    print("  - Stop: Process and summarize conversation")
    return True


def print_usage():
    """打印使用说明"""
    python_exe = get_python_executable()
    dashboard_path = PROJECT_ROOT / "src" / "http_api" / "dashboard.py"

    print("\n" + "=" * 60)
    print("Installation Complete!")
    print("=" * 60)

    if sys.platform == "win32":
        activate_cmd = f"{VENV_DIR}\\Scripts\\activate"
    else:
        activate_cmd = f"source {VENV_DIR}/bin/activate"

    print(f"""
Usage:

1. Activate virtual environment (optional, for manual commands):
   {activate_cmd}

2. Start the Dashboard:
   "{python_exe}" "{dashboard_path}"

3. Open in browser:
   http://localhost:37888

4. Hooks will automatically activate when you start Claude Code
   (They use the virtual environment's Python)

To uninstall hooks:
   python install.py --uninstall
""")

    # Mac/Linux 故障排除提示
    if sys.platform != "win32":
        hooks_dir = PROJECT_ROOT / ".claude" / "hooks"
        print(f"""Troubleshooting (Mac/Linux):

If hooks fail with "permission denied", run:
   chmod +x {hooks_dir}/*.py
""")

    print("=" * 60)


def uninstall_hooks():
    """移除 hooks 配置"""
    print("\nUninstalling Hybrid Memory hooks...")
    settings_path = get_claude_settings_path()

    if not settings_path.exists():
        print("No settings file found. Nothing to uninstall.")
        return

    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    hooks = settings.get("hooks", {})
    removed = []
    for hook_name in ["SessionStart", "UserPromptSubmit", "Stop"]:
        if hook_name in hooks:
            hook_list = hooks[hook_name]
            if any("hybrid-memory" in str(h) for h in hook_list):
                del hooks[hook_name]
                removed.append(hook_name)

    if removed:
        settings["hooks"] = hooks
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        print(f"Removed hooks: {', '.join(removed)}")
    else:
        print("No Hybrid Memory hooks found.")


def main():
    print("=" * 60)
    print("Hybrid Memory Installation")
    print("=" * 60)
    print(f"Project: {PROJECT_ROOT}")
    print(f"Python:  {sys.version}")

    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        uninstall_hooks()
        return

    if not create_venv():
        sys.exit(1)

    if not install_dependencies():
        sys.exit(1)

    if not setup_data_dir():
        sys.exit(1)

    if not configure_hooks():
        sys.exit(1)

    print_usage()


if __name__ == "__main__":
    main()

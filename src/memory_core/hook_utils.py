"""
Hook 共享工具模块

所有 Claude Code hooks 的公共代码，包括：
- 路径配置
- Logger 配置
- 项目名和数据库路径获取
- UTF-8 编码配置
- 文本清理
"""
import sys
import os
from pathlib import Path
from loguru import logger

# 路径配置（相对于项目根目录）
MEMORY_BASE = Path(__file__).parent.parent.parent / "data"
GLOBAL_DB = MEMORY_BASE / "global_memory.db"
PROJECTS_DIR = MEMORY_BASE / "projects"
LOG_FILE = MEMORY_BASE / "hooks.log"


def setup_hook_logger():
    """配置 hook 日志输出"""
    logger.remove()
    MEMORY_BASE.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(LOG_FILE, level="DEBUG", rotation="1 MB", retention="1 hour")


def configure_utf8_stdio():
    """配置 Windows UTF-8 编码"""
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')


def get_project_name() -> str:
    """从当前工作目录获取项目名"""
    return Path(os.getcwd()).name


def get_project_db_path(project_name: str) -> Path:
    """获取项目级数据库路径"""
    return PROJECTS_DIR / f"{project_name}.db"


def sanitize_text(text: str) -> str:
    """移除无效的 surrogate 字符"""
    return text.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')

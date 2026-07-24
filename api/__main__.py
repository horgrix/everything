"""
API 模块入口：`python -m api` 启动纯 API 模式。
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="爬虫系统 HTTP API 服务")
    parser.add_argument("--port", "-p", type=int, default=8000, help="监听端口 (默认: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    parser.add_argument("--config", "-c", default="config/tasks", help="任务配置目录")
    parser.add_argument("--db", "-d", default="crawler.db", help="数据库路径")
    args = parser.parse_args()

    # 确保 uvicorn 能导入 app
    import os
    os.environ.setdefault("API_CONFIG_DIR", args.config)
    os.environ.setdefault("API_DB_PATH", args.db)

    uvicorn.run(
        "api:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
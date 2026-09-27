import logging

from flask import Flask

from auth import register_auth
from config import Config
from pancheck import pancheck_bp
from proxy import proxy_bp

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    """创建 Flask 应用。"""
    Config.ensure_runtime_defaults()
    Config.validate()
    app = Flask(__name__)
    register_auth(app)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(pancheck_bp)
    return app


def load_app():
    try:
        return create_app()
    except ValueError as e:
        logger.error(f"配置验证失败: {e}")
        raise


app = load_app()


if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)

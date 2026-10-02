"""众生之门 服务端（Flask 应用工厂）。"""

from __future__ import annotations

from flask import Flask, jsonify

from .config import Config
from .errors import ApiError


def create_app(config: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    # 中文不转义成 \\uXXXX
    app.json.ensure_ascii = False

    from .api.v1 import bp as v1_bp

    app.register_blueprint(v1_bp)

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify(exc.to_dict()), exc.status

    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify(ok=False, code="NOT_FOUND", msg="接口不存在"), 404

    @app.errorhandler(405)
    def _bad_method(_exc):
        return jsonify(ok=False, code="BAD_REQUEST", msg="请求方法不允许"), 405

    @app.errorhandler(Exception)
    def _internal(exc: Exception):
        app.logger.exception("未处理异常: %s", exc)
        return jsonify(ok=False, code="INTERNAL", msg="服务器内部错误"), 500

    return app

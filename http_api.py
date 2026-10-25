"""HTTP 旁路接口：发现（告诉客户端该连哪里）+ 健康检查。

与旧版的差别：

* ``host`` / ``port`` **只从 .env 读**，不再请求 api.ipify.org
  （旧版在 import 期拉公网 IP，拉不到就整个进程起不来）；
* 路径从 ``/game/api`` 改成 ``/api/v1/endpoint``，并额外返回版本与推送频率；
* 新增 ``/healthz``，可直接给负载均衡 / 监控用。

真正的游戏通信全走 TCP（见 `server.py`），HTTP 只做发现。
"""

from __future__ import annotations

import logging
import time

import flask

import config

log = logging.getLogger(__name__)


def create_app(cfg: config.Config, store, game_server=None) -> flask.Flask:
    """构造 Flask 应用。"""
    app = flask.Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    started_at = time.time()

    def _endpoint_payload() -> dict:
        return {
            "tcp": {
                "host": cfg.public_host,
                "port": cfg.public_port,
            },
            # 兼容旧客户端字段名
            "host": cfg.public_host,
            "port": cfg.public_port,
            "protocol_version": config.PROTOCOL_VERSION,
            "service_version": config.SERVICE_VERSION,
            "push_hz": cfg.push_hz,
            "transport": "tcp",
            "encoding": "utf-8",
        }

    @app.get("/")
    def index():
        return flask.jsonify({
            "service": "The Door of All Beings",
            "service_version": config.SERVICE_VERSION,
            "protocol_version": config.PROTOCOL_VERSION,
            "endpoint": "/api/v1/endpoint",
            "health": "/healthz",
        })

    @app.route("/api/v1/endpoint", methods=["GET", "POST"])
    def endpoint():
        return flask.jsonify(_endpoint_payload())

    # 旧客户端的发现路径，保留一个版本周期做过渡
    @app.route("/game/api", methods=["GET", "POST"])
    def legacy_endpoint():
        return flask.jsonify(_endpoint_payload())

    @app.get("/healthz")
    def healthz():
        db_ok = True
        try:
            db_ok = bool(store.ping())
        except Exception:  # noqa: BLE001 - 探活失败就是 False
            db_ok = False
        payload = {
            "ok": db_ok,
            "db": {"ok": db_ok, "backend": getattr(store, "backend_name", "?")},
            "service_version": config.SERVICE_VERSION,
            "protocol_version": config.PROTOCOL_VERSION,
            "uptime": round(time.time() - started_at, 1),
        }
        if game_server is not None:
            payload["runtime"] = game_server.snapshot()
        return flask.jsonify(payload), (200 if db_ok else 503)

    @app.errorhandler(404)
    def not_found(_err):
        return flask.jsonify({"ok": False, "error": "NOT_FOUND"}), 404

    return app


def serve(cfg: config.Config, app: flask.Flask) -> None:
    """启动 HTTP 服务（阻塞）。生产走 waitress，DEBUG 才用 Werkzeug。"""
    if cfg.debug:
        log.warning("DEBUG 模式：HTTP 用 Werkzeug 开发服务器（不要用于生产）")
        app.run(
            host=cfg.http_bind,
            port=cfg.http_port,
            debug=False,       # reloader 会 fork 进程，和 TCP 服务抢端口，必须关掉
            use_reloader=False,
            threaded=True,
        )
        return

    try:
        from waitress import serve as waitress_serve
    except ImportError:  # pragma: no cover - 依赖缺失时降级
        log.warning("waitress 未安装，降级为 Werkzeug")
        app.run(host=cfg.http_bind, port=cfg.http_port, threaded=True)
        return

    waitress_serve(app, host=cfg.http_bind, port=cfg.http_port, threads=8)

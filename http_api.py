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
import threading
import time

import flask

import config

log = logging.getLogger(__name__)

## 后台 HTTP 服务的句柄（给 s3 启动、s4 关闭用）
_server = None
_server_lock = threading.Lock()


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
    """启动 HTTP 服务（**阻塞**）。只给单独调试用。"""
    server = _make_server(cfg, app)
    try:
        server.run()
    finally:
        _close(server)


def start_background(cfg: config.Config, app: flask.Flask) -> None:
    """s3：后台线程跑 HTTP（非阻塞，幂等）。

    用 ``create_server()`` 而不是 ``serve()``，因为前者返回可关闭的句柄：
    s4 关闭时必须能把监听断开，否则进程会卡在非 daemon 的 HTTP 线程上退不出去。
    """
    global _server
    with _server_lock:
        if _server is not None:
            return
        server = _make_server(cfg, app)
        _server = server
        threading.Thread(
            target=_run_quietly,
            args=(server,),
            name="http",
            daemon=True,
        ).start()
    log.info("HTTP 已监听 %s:%s", cfg.http_bind, cfg.http_port)


def shutdown() -> None:
    """s4：关闭 HTTP 监听（幂等；未启动时调用也安全）。"""
    global _server
    with _server_lock:
        server = _server
        _server = None
    if server is not None:
        _close(server)
        log.info("HTTP 已停止")


def _run_quietly(server) -> None:
    try:
        server.run()
    except Exception:  # noqa: BLE001 - 退出时 close() 会让 run() 抛异常，属正常
        log.debug("HTTP 服务线程退出", exc_info=True)


def _close(server) -> None:
    try:
        server.close()
    except Exception:  # noqa: BLE001
        pass


def _make_server(cfg: config.Config, app: flask.Flask):
    """构造一个可关闭的 HTTP server（生产 waitress，DEBUG 用 Werkzeug）。"""
    if cfg.debug:
        log.warning("DEBUG 模式：HTTP 用 Werkzeug 开发服务器（不要用于生产）")
        from werkzeug.serving import make_server

        return make_server(
            cfg.http_bind,
            cfg.http_port,
            app,
            threaded=True,
        )

    try:
        from waitress import create_server
    except ImportError:  # pragma: no cover - 依赖缺失时降级
        log.warning("waitress 未安装，降级为 Werkzeug")
        from werkzeug.serving import make_server

        return make_server(cfg.http_bind, cfg.http_port, app, threaded=True)

    return create_server(
        app,
        host=cfg.http_bind,
        port=cfg.http_port,
        threads=8,
    )

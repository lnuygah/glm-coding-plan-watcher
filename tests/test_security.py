from __future__ import annotations

from glm_plan_watcher.daemon.security import bind_server_socket, bind_with_port_fallback


def test_bind_with_port_fallback_when_port_in_use() -> None:
    # 先占住一个端口。
    occupied = bind_server_socket("127.0.0.1", 0)
    port = occupied.getsockname()[1]
    try:
        # 请求被占用的端口 → 回退到另一个空闲端口（不抛错）。
        fallback = bind_with_port_fallback("127.0.0.1", port)
        try:
            assert fallback.getsockname()[1] != port
        finally:
            fallback.close()
    finally:
        occupied.close()


def test_bind_with_port_fallback_uses_requested_free_port() -> None:
    # 端口空闲时直接用请求端口（这里用 0=自动分配，确保不抛错并能拿到端口）。
    sock = bind_with_port_fallback("127.0.0.1", 0)
    try:
        assert sock.getsockname()[1] > 0
    finally:
        sock.close()

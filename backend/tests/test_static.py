"""静态页挂载回归测试。

T5 交付了 backend/static/join.html，但没有任何任务负责把它挂到路由上，
README 却写着 open http://localhost:8000/join。这个测试锁死该集成点。
"""

from fastapi.testclient import TestClient

from app.main import app


def test_join_page_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/join")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_join_page_contains_registration_form() -> None:
    with TestClient(app) as client:
        body = client.get("/join").text
    # 页面必须真的是登记页，不是空壳或错误页
    assert "steam" in body.lower()
    # API 基址 + players 端点（页面内是 API="/api" 与 "/players" 拼接）
    assert 'var API = "/api"' in body
    assert "/players/resolve/" in body

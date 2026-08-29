"""Memory /metrics self-exposure route tests."""

import asyncio
import subprocess

import httpx

import katana_memory_mcp.server as server


def test_metrics_route_exposes_process_up_metric(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    subprocess.run(["git", "init"], cwd=data_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=data_root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=data_root, check=True)
    app = server.build_app(str(data_root))

    async def _get_metrics():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/metrics")

    response = asyncio.run(_get_metrics())

    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert content_type.startswith("text/plain")
    assert "version=0.0.4" in content_type
    assert "katana_memory_up 1" in response.text

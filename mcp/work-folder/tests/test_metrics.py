"""Work Folder /metrics self-exposure custom route tests."""

import asyncio

import httpx

import katana_work_folder_mcp.server as server


def test_metrics_route_exposes_process_up_metric():
    async def _get_metrics():
        app = server.mcp.http_app()
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
    assert "katana_work_folder_up 1" in response.text
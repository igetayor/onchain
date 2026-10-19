import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_probe(client: AsyncClient):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("UP", "HEALTHY")
    assert "database" in data
    assert "redis" in data


@pytest.mark.asyncio
async def test_account_write_through_and_read_through(client: AsyncClient):
    payload = {
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "network": "ethereum-mainnet",
        "label": "Cold Storage",
        "balance": 500.25,
        "is_active": True,
    }

    # 1. Create account (Write-Through)
    post_res = await client.post("/api/v1/accounts", json=payload)
    assert post_res.status_code == 201
    post_data = post_res.json()
    assert post_data["cache_meta"]["cache_status"] == "WRITE_THROUGH"
    assert post_data["data"]["address"] == payload["address"]

    # 2. Read account (Read-Through - First read is cache HIT because of Write-Through!)
    get_res = await client.get(f"/api/v1/accounts/{payload['address']}")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert "HIT" in get_data["cache_meta"]["cache_status"]
    assert "X-Process-Time-Ms" in get_res.headers
    assert "X-Correlation-ID" in get_res.headers
    assert float(get_res.headers["X-Process-Time-Ms"]) >= 0.0


@pytest.mark.asyncio
async def test_account_update_write_through(client: AsyncClient):
    addr = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    # Create
    await client.post(
        "/api/v1/accounts",
        json={"address": addr, "network": "polygon", "balance": 100.0},
    )

    # Update (Write-Through)
    put_res = await client.put(
        f"/api/v1/accounts/{addr}",
        json={"balance": 250.0, "label": "Updated Treasury"},
    )
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["data"]["balance"] == 250.0
    assert put_data["cache_meta"]["cache_status"] == "WRITE_THROUGH"

    # Verify cached read reflects updated balance immediately
    get_res = await client.get(f"/api/v1/accounts/{addr}")
    assert get_res.status_code == 200
    assert get_res.json()["data"]["balance"] == 250.0


@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/metrics")
    assert response.status_code == 200
    metrics = response.json()
    assert "total_reads" in metrics
    assert "cache_hits" in metrics
    assert "cache_hit_ratio_pct" in metrics
    assert "stampedes_prevented" in metrics

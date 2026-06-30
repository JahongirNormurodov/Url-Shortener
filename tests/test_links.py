"""Havola yaratish/redirect/CRUD va egalik testlari."""


async def test_shorten_and_redirect(auth_client):
    resp = await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/page"})
    assert resp.status_code == 201
    body = resp.json()
    code = body["code"]
    assert len(code) == 7  # avtomatik base62 kod, 7 belgi
    assert body["short_url"].endswith(f"/{code}")

    # Redirect — 302 va Location asl manzilga.
    # follow_redirects=False: yo'naltirishni avtomatik kuzatmaymiz.
    r = await auth_client.get(f"/{code}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://example.com/page"


async def test_custom_alias(auth_client):
    resp = await auth_client.post(
        "/api/v1/shorten",
        json={"url": "https://example.com/x", "custom_alias": "my-link"},
    )
    assert resp.status_code == 201
    assert resp.json()["code"] == "my-link"

    # Bir xil aliasni qayta olish — 409.
    dup = await auth_client.post(
        "/api/v1/shorten",
        json={"url": "https://example.com/y", "custom_alias": "my-link"},
    )
    assert dup.status_code == 409


async def test_idempotent_shorten(auth_client):
    first = await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/same"})
    second = await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/same"})
    assert first.json()["code"] == second.json()["code"]


async def test_reject_unsafe_url(auth_client):
    # javascript: sxemasi rad etiladi (400).
    resp = await auth_client.post("/api/v1/shorten", json={"url": "javascript:alert(1)"})
    assert resp.status_code == 400


async def test_reject_localhost_ssrf(auth_client):
    resp = await auth_client.post("/api/v1/shorten", json={"url": "http://127.0.0.1:8080/admin"})
    assert resp.status_code == 400


async def test_redirect_unknown_code_404(client):
    r = await client.get("/nope123", follow_redirects=False)
    assert r.status_code == 404


async def test_soft_delete_returns_410(auth_client):
    resp = await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/del"})
    code = resp.json()["code"]

    deleted = await auth_client.delete(f"/api/v1/links/{code}")
    assert deleted.status_code == 204

    # Soft delete'dan keyin redirect 410 (Gone), lekin metadata saqlanadi.
    r = await auth_client.get(f"/{code}", follow_redirects=False)
    assert r.status_code == 410


async def test_list_and_get_links(auth_client):
    await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/a"})
    await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/b"})

    listing = await auth_client.get("/api/v1/links")
    assert listing.status_code == 200
    data = listing.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


async def test_update_link(auth_client):
    resp = await auth_client.post("/api/v1/shorten", json={"url": "https://example.com/old"})
    code = resp.json()["code"]

    patch = await auth_client.patch(
        f"/api/v1/links/{code}", json={"long_url": "https://example.com/new"}
    )
    assert patch.status_code == 200
    assert patch.json()["long_url"] == "https://example.com/new"

    r = await auth_client.get(f"/{code}", follow_redirects=False)
    assert r.headers["location"] == "https://example.com/new"


async def test_ownership_isolation(client):
    # Foydalanuvchi A havola yaratadi.
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": "password123"}
    )
    a_login = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "password123"}
    )
    a_token = a_login.json()["access_token"]
    made = await client.post(
        "/api/v1/shorten",
        json={"url": "https://example.com/private"},
        headers={"Authorization": f"Bearer {a_token}"},
    )
    code = made.json()["code"]

    # Foydalanuvchi B uni ko'ra olmaydi (404 — egalik filtri).
    await client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "password": "password123"}
    )
    b_login = await client.post(
        "/api/v1/auth/login", json={"email": "b@example.com", "password": "password123"}
    )
    b_token = b_login.json()["access_token"]
    seen = await client.get(
        f"/api/v1/links/{code}", headers={"Authorization": f"Bearer {b_token}"}
    )
    assert seen.status_code == 404

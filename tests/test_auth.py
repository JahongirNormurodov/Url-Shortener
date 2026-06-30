"""Autentifikatsiya oqimi testlari (register/login/refresh/logout/me)."""


async def test_register_success(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "ali@example.com", "password": "password123", "display_name": "Ali"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "ali@example.com"
    assert body["display_name"] == "Ali"
    # Parol/hash hech qachon javobда bo'lmasligi kerak.
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_duplicate_email_409(client):
    payload = {"email": "dup@example.com", "password": "password123"}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409


async def test_register_weak_password_422(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "123"},
    )
    assert resp.status_code == 422


async def test_login_and_access_me(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "me@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] > 0

    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "me@example.com"


async def test_login_wrong_password_401(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "password123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "x@example.com", "password": "wrongpass1"},
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/me")
    assert resp.status_code == 401


async def test_refresh_rotates_token(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "r@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "r@example.com", "password": "password123"},
    )
    old_refresh = login.json()["refresh_token"]

    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != old_refresh

    # Eski refresh endi ishlamaydi (bekor qilingan).
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401


async def test_logout_revokes_refresh(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "lo@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "lo@example.com", "password": "password123"},
    )
    tokens = login.json()

    out = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert out.status_code == 204

    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401


async def test_update_me_and_change_password(auth_client):
    patch = await auth_client.patch("/api/v1/me", json={"display_name": "Yangi Ism"})
    assert patch.status_code == 200
    assert patch.json()["display_name"] == "Yangi Ism"

    chg = await auth_client.post(
        "/api/v1/me/change-password",
        json={"current_password": "password123", "new_password": "newpassword456"},
    )
    assert chg.status_code == 204

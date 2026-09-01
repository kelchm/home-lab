def test_healthz_is_open(client):
    assert client.get("/admin/v1/healthz").status_code == 200


def test_state_requires_token(client, auth):
    assert client.get("/admin/v1/state").status_code == 401
    assert client.get("/admin/v1/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/admin/v1/state", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["resident"] == "glm"
    assert body["default_profile"] == "qwen"
    assert body["profiles"] == ["glm", "qwen"]


def test_profiles_come_from_checkout(client, auth):
    profiles = client.get("/admin/v1/profiles", headers=auth).json()
    assert profiles["glm"]["served_model_id"] == "GLM-5.3-Flash-EXL3"


def test_switch_rejects_unknown_profile(client, auth):
    response = client.post("/admin/v1/switches", json={"profile": "nope"}, headers=auth)
    assert response.status_code == 422

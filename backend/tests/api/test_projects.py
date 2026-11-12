import uuid

import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER

from .conftest import TOKEN

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def test_projects_are_created_listed_read_and_renamed(api):
    async with api() as (client, _):
        first = (await client.post("/api/v1/projects", json={"name": "Attention"})).json()
        created = await client.post("/api/v1/projects", json={"name": "Retrieval", "description": "RAG"})
        assert created.status_code == 201
        second = created.json()
        assert second["description"] == "RAG" and second["created_at"]
        assert [p["id"] for p in (await client.get("/api/v1/projects")).json()] == [second["id"], first["id"]]
        assert (await client.get(f"/api/v1/projects/{first['id']}")).json()["name"] == "Attention"
        renamed = await client.patch(f"/api/v1/projects/{first['id']}", json={"name": "Transformers"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "Transformers"
        assert (await client.post("/api/v1/projects", json={"name": ""})).status_code == 422
        assert (await client.get(f"/api/v1/projects/{uuid.uuid4()}")).status_code == 404
        assert (await client.patch(f"/api/v1/projects/{uuid.uuid4()}", json={"name": "x"})).status_code == 404


async def test_threads_list_newest_first_and_show_their_messages(api):
    async with api() as (client, m):
        project = (await client.post("/api/v1/projects", json={"name": "p"})).json()
        url = f"/api/v1/projects/{project['id']}/threads"
        first = (await client.post(url, json={"title": "one"})).json()
        created = await client.post(url, json={})
        assert created.status_code == 201
        second = created.json()
        assert second["title"] is None and second["project_id"] == project["id"]
        assert [t["id"] for t in (await client.get(url)).json()] == [second["id"], first["id"]]
        run = await m.create(uuid.UUID(project["id"]), uuid.UUID(first["id"]), "Why?")
        detail = (await client.get(f"/api/v1/threads/{first['id']}")).json()
        assert detail["title"] == "one"
        assert [(x["role"], x["text"], x["run_id"]) for x in detail["messages"]] == [("user", "Why?", str(run.id))]
        assert (await client.post(f"/api/v1/projects/{uuid.uuid4()}/threads", json={})).status_code == 404
        assert (await client.get(f"/api/v1/threads/{uuid.uuid4()}")).status_code == 404


async def test_the_api_answers_503_without_a_run_manager():
    app = create_app(Settings(env="test"), token=TOKEN)  # no lifespan ran, so there is no manager
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/v1/projects", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 503

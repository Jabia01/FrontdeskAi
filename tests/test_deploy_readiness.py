"""What a managed platform needs from this app.

None of it is exotic: credentials from the environment rather than a file, a
health check the platform can act on, and an image that actually contains the
templates. All three fail *quietly* when they are wrong, so each is asserted
here rather than discovered on a deploy.
"""

from __future__ import annotations

import pathlib

import pytest

from app.config import get_settings

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestHealthCheck:
    async def test_it_reports_the_database_as_reachable(self, client, db):
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"

    async def test_it_reports_what_is_configured(self, client, db):
        body = (await client.get("/health")).json()
        for key in (
            "channels",
            "ai_enabled",
            "anthropic_key_configured",
            "twilio_configured",
            "calendar_configured",
            "queue",
        ):
            assert key in body, key

    async def test_an_unreachable_database_is_a_503(self, client, monkeypatch):
        """The platform's health check has to be able to fail a deploy. A 200
        with a broken database means every webhook 500s one at a time."""
        from app import main

        class Boom:
            async def __aenter__(self):
                raise OSError("connection refused")

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(main, "session_scope", lambda: Boom())
        response = await client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert "error" in body["database"]


class TestCredentialsFromTheEnvironment:
    def test_inline_google_json_counts_as_configured(self, monkeypatch):
        """A platform with no persistent disk cannot give us a file path."""
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}')
        get_settings.cache_clear()
        try:
            assert get_settings().calendar_configured is True
        finally:
            monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
            get_settings.cache_clear()

    def test_inline_json_is_preferred_over_a_path(self, monkeypatch):
        from app.integrations.calendar import (
            GoogleCalendarClient,
            get_calendar_client,
            set_calendar_client,
        )

        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}')
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/nonexistent/key.json")
        get_settings.cache_clear()
        set_calendar_client(None)
        try:
            client = get_calendar_client()
            assert isinstance(client, GoogleCalendarClient)
            assert client._credentials_json
            assert not client._credentials_path
        finally:
            monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
            monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
            get_settings.cache_clear()
            set_calendar_client(None)

    def test_no_credentials_still_falls_back_to_the_stub(self):
        from app.integrations.calendar import (
            StubCalendarClient,
            get_calendar_client,
            set_calendar_client,
        )

        get_settings.cache_clear()
        set_calendar_client(None)
        try:
            assert isinstance(get_calendar_client(), StubCalendarClient)
        finally:
            set_calendar_client(None)

    def test_the_pooler_knob_disables_both_statement_caches(self, monkeypatch):
        """Behind a transaction-mode pooler, SQLAlchemy's cache and asyncpg's
        own cache both have to go, or prepared statements fail intermittently
        under concurrency - the worst kind of production bug."""
        import inspect

        from app import db

        source = inspect.getsource(db.get_engine)
        assert "statement_cache_size" in source
        assert "prepared_statement_cache_size" in source

        monkeypatch.setenv("DB_DISABLE_PREPARED_STATEMENTS", "true")
        get_settings.cache_clear()
        try:
            assert get_settings().db_disable_prepared_statements is True
        finally:
            monkeypatch.delenv("DB_DISABLE_PREPARED_STATEMENTS", raising=False)
            get_settings.cache_clear()


class TestTheImageShipsEverythingItNeeds:
    """The templates and the stylesheet are not Python modules, so a plain
    `pip install .` would leave them out of the image - the console would start
    and then 500 on its first page."""

    def test_the_dockerfile_installs_editable_from_the_source_tree(self):
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "pip install --no-cache-dir -e ." in dockerfile
        assert "PYTHONUNBUFFERED=1" in dockerfile

    def test_the_dockerfile_copies_what_the_app_reads_at_runtime(self):
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        for needed in (
            "COPY app ./app",
            "COPY migrations ./migrations",
            "alembic.ini",
        ):
            assert needed in dockerfile, needed

    def test_the_template_and_static_directories_are_where_the_code_looks(self):
        from app.web.templating import STATIC_DIR, TEMPLATE_DIR

        assert (TEMPLATE_DIR / "base.html").is_file()
        assert (STATIC_DIR / "app.css").is_file()
        assert (TEMPLATE_DIR / "admin" / "shops.html").is_file()

    def test_dockerignore_keeps_secrets_out_of_the_image(self):
        ignored = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        assert ".env" in ignored
        assert ".venv/" in ignored


class TestTheBlueprint:
    @pytest.fixture(scope="class")
    def blueprint(self):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load((REPO / "render.yaml").read_text(encoding="utf-8"))

    def test_it_declares_a_web_service_a_worker_and_a_key_value_store(
        self, blueprint
    ):
        types = {service["type"] for service in blueprint["services"]}
        assert types == {"web", "worker", "keyvalue"}

    def test_the_worker_runs_arq(self, blueprint):
        worker = next(s for s in blueprint["services"] if s["type"] == "worker")
        assert "arq app.queue.worker.WorkerSettings" in worker["dockerCommand"]

    def test_the_web_service_runs_migrations_before_taking_traffic(self, blueprint):
        web = next(s for s in blueprint["services"] if s["type"] == "web")
        assert "alembic upgrade head" in web["preDeployCommand"]
        assert web["healthCheckPath"] == "/health"

    def test_the_web_service_trusts_the_proxy(self, blueprint):
        """Without this, the signup form's per-address rate limit sees every
        visitor as the proxy."""
        web = next(s for s in blueprint["services"] if s["type"] == "web")
        assert "--proxy-headers" in web["dockerCommand"]

    def test_no_secret_is_committed(self, blueprint):
        secrets = {
            "DATABASE_URL",
            "ANTHROPIC_API_KEY",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "GOOGLE_CREDENTIALS_JSON",
            "PUBLIC_BASE_URL",
        }
        for service in blueprint["services"]:
            for env in service.get("envVars", []):
                if env["key"] in secrets:
                    assert env.get("sync") is False, env["key"]
                    assert "value" not in env, env["key"]

    def test_both_processes_get_the_same_credentials(self, blueprint):
        """The worker answers customers too. A credential the web service has
        and the worker does not is a failure that only happens in the
        background, where nobody is looking."""
        web, worker = (
            next(s for s in blueprint["services"] if s["type"] == kind)
            for kind in ("web", "worker")
        )
        needed = {
            "DATABASE_URL",
            "ANTHROPIC_API_KEY",
            "REDIS_URL",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
        }
        for service in (web, worker):
            keys = {env["key"] for env in service["envVars"]}
            assert needed <= keys, (service["name"], needed - keys)

    def test_the_queue_never_evicts_a_job(self, blueprint):
        """An evicted job is a customer who never got an answer."""
        store = next(s for s in blueprint["services"] if s["type"] == "keyvalue")
        assert store["maxmemoryPolicy"] == "noeviction"

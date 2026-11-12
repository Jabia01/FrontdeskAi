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

    @pytest.fixture(scope="class")
    def web(self, blueprint):
        return next(s for s in blueprint["services"] if s["type"] == "web")

    def test_it_is_a_single_always_on_service(self, blueprint, web):
        """One service, because two cost twice as much. Deliberately not the
        `free` plan: a free service sleeps, and a sleeping webhook receiver
        means Twilio times out and the morning's first text is lost."""
        assert [s["type"] for s in blueprint["services"]] == ["web"]
        assert web["plan"] != "free"

    def test_the_worker_runs_inside_it(self, web):
        env = {e["key"]: e.get("value") for e in web["envVars"]}
        assert env["RUN_WORKER_IN_PROCESS"] == "true"

    def test_only_one_instance(self, web):
        """A second instance would run a second copy of the cron sweeps."""
        env = {e["key"]: e.get("value") for e in web["envVars"]}
        assert env["WEB_CONCURRENCY"] == "1"

    def test_it_runs_migrations_before_taking_traffic(self, web):
        assert "alembic upgrade head" in web["preDeployCommand"]
        assert web["healthCheckPath"] == "/health"

    def test_it_trusts_the_proxy(self, web):
        """Without this the signup form's per-address rate limit sees every
        visitor as the proxy."""
        assert "--proxy-headers" in web["dockerCommand"]

    def test_no_secret_is_committed(self, web):
        secrets = {
            "DATABASE_URL",
            "REDIS_URL",
            "ANTHROPIC_API_KEY",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "GOOGLE_CREDENTIALS_JSON",
            "PUBLIC_BASE_URL",
        }
        provided = {e["key"] for e in web["envVars"]}
        assert secrets <= provided, secrets - provided
        for env in web["envVars"]:
            if env["key"] in secrets:
                assert env.get("sync") is False, env["key"]
                assert "value" not in env, env["key"]

    def test_the_standalone_worker_entrypoint_still_exists(self):
        """Splitting the processes again must stay a config change, not a
        rewrite - so the standalone worker keeps working."""
        from app.queue.worker import WorkerSettings

        assert len(WorkerSettings.functions) >= 5
        assert len(WorkerSettings.cron_jobs) == 2


class TestTheEmbeddedWorker:
    """The single-service deployment runs the queue inside the web process."""

    async def test_it_is_off_unless_asked_for(self, client, db):
        body = (await client.get("/health")).json()
        assert body["worker"] == {"embedded": False}

    async def test_signals_stay_with_the_server(self):
        """arq installs its own SIGTERM handler by default. Sharing a process
        with uvicorn, that makes shutdown unpredictable."""
        import inspect

        from app.queue import embedded

        source = inspect.getsource(embedded.EmbeddedWorker.start)
        assert "handle_signals=False" in source

    async def test_it_does_not_dispose_the_shared_engine(self):
        """The standalone worker's shutdown hook disposes the SQLAlchemy
        engine. In a shared process that engine belongs to the web app too."""
        import inspect

        from app.queue import embedded

        source = inspect.getsource(embedded.EmbeddedWorker.start)
        assert "on_shutdown=None" in source

    async def test_a_dead_worker_makes_the_service_unhealthy(self, client, db):
        """Otherwise the failure is silent: replies queue up forever and
        nothing tells anyone."""
        import asyncio

        from app.queue.embedded import EmbeddedWorker

        worker = EmbeddedWorker()

        async def finished():
            return None

        worker._task = asyncio.create_task(finished())
        await asyncio.sleep(0)
        assert worker.started is True
        assert worker.alive is False

        from app.main import app

        previous = getattr(app.state, "worker", None)
        app.state.worker = worker
        try:
            response = await client.get("/health")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "degraded"
            assert body["worker"]["embedded"] is True
            assert body["worker"]["alive"] is False
        finally:
            app.state.worker = previous

    async def test_stopping_a_worker_that_never_started_is_harmless(self):
        from app.queue.embedded import EmbeddedWorker

        await EmbeddedWorker().stop()


class TestHealthLeaksNoSecrets:
    """`/health` is public and unauthenticated, and it was serving the Redis
    DSN - password and all - to anyone who asked. It is the one endpoint on
    this app with no login in front of it, so everything it returns is
    published."""

    async def test_no_credential_appears_in_the_payload(
        self, client, db, monkeypatch
    ):
        from app.config import get_settings

        secret = "super-secret-password-9z8y7x"
        monkeypatch.setenv(
            "REDIS_URL", f"rediss://default:{secret}@fake.upstash.io:6379"
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key-123")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-token-abcdef")
        get_settings.cache_clear()
        try:
            response = await client.get("/health")
            body = response.text
        finally:
            get_settings.cache_clear()

        for leaked in (secret, "sk-ant-not-a-real-key-123", "twilio-token-abcdef"):
            assert leaked not in body, f"/health leaked {leaked[:12]}..."
        # The host is fine and useful - it says which environment this is.
        assert "fake.upstash.io" in body

    async def test_the_database_url_never_appears_either(self, client, db):
        from app.config import get_settings

        body = (await client.get("/health")).text
        url = get_settings().database_url
        assert url not in body
        # Not even the password portion on its own.
        from urllib.parse import urlsplit

        password = urlsplit(url).password
        if password:
            assert password not in body

    def test_describe_pool_returns_a_host_not_a_dsn(self):
        from app.queue.client import describe_pool

        described = describe_pool()
        assert "redis_url" not in described
        assert "://" not in str(described.get("host", ""))

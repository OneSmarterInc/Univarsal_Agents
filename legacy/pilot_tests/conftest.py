import io

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from extraction_service.api import create_app
from extraction_service.config import Settings
from extraction_service.service import Service


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_url="sqlite:///" + str(tmp_path / "db.sqlite"),
        data_dir=tmp_path / "data",
        key_pepper="synthetic-test-pepper-" * 3,
        token_encryption_key=Fernet.generate_key().decode(),
        ollama_digest="a" * 64,
        model_artifact_sha256="b" * 64,
    )
    service = Service(settings)
    service.db.initialize()
    return service


@pytest.fixture
def client(service):
    return TestClient(create_app(service=service), raise_server_exceptions=False)


@pytest.fixture
def token(service):
    return service.create_key("tenant-a")


@pytest.fixture
def actor(service, token):
    return service.authenticate(token)


@pytest.fixture
def pdf():
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(50, 750, "Alex Example")
    c.drawString(50, 720, "Python developer. Education: Example University, Computer Science, 2024.")
    c.save()
    return buffer.getvalue()

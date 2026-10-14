import argparse
import json
import secrets

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(prog="agents")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser(
        "generate-secrets", help="Print secrets once to your private terminal, never to shared logs"
    )
    key = commands.add_parser("create-key")
    key.add_argument("--tenant", required=True)
    key.add_argument("--scopes", nargs="*")
    revoke = commands.add_parser("revoke-key")
    revoke.add_argument("--id", required=True)
    commands.add_parser("sweep")
    commands.add_parser("worker")
    commands.add_parser("model-check")
    commands.add_parser(
        "reset-inference", help="Only after operator verifies no abandoned inference is running"
    )
    args = parser.parse_args()
    if args.command == "generate-secrets":
        print("AGENTS_KEY_PEPPER=" + secrets.token_urlsafe(48))
        print("AGENTS_TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
        return
    from .config import Settings
    from .service import Service

    service = Service(Settings())
    if args.command == "init-db":
        service.db.initialize()
    elif args.command == "create-key":
        print(service.create_key(args.tenant, args.scopes))
    elif args.command == "revoke-key":
        from .db import ApiKey

        with service.db.transaction() as db:
            row = db.get(ApiKey, args.id)
            if row:
                row.revoked = True
    elif args.command == "sweep":
        service.sweep()
    elif args.command == "worker":
        from .worker import main as worker

        worker()
    elif args.command == "reset-inference":
        (service.settings.data_dir / "inference.blocked").unlink(missing_ok=True)
    elif args.command == "model-check":
        from .errors import ServiceError
        from .inference import Ollama

        model = Ollama(service.settings)
        try:
            identity = model.ready()
            result = model.extract("cv", {"id": "synthetic", "text": "Name: Alex Example"})
            if not any(c.field == "name" and c.value == "Alex Example" for c in result.scalars):
                raise ServiceError("synthetic_model_check_failed")
            print(json.dumps({"status": "synthetic_check_passed", **identity}))
        finally:
            model.client.close()


if __name__ == "__main__":
    main()

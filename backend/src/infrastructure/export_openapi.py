"""Print the API's OpenAPI document to stdout.

    uv run python -m src.infrastructure.export_openapi > openapi.json

The frontend's types are generated from this (`npm run gen:api`). Doing it here
rather than curling a running server means regenerating needs no server and no
database — the schema is a property of the code, not of a deployment, and asking a
contributor to boot Postgres to refresh a type file is how the type file ends up
never refreshed.

`create_app()` only wires routers; the lifespan (which does touch the database) is
not run by asking the app for its schema.
"""
import json

from main import create_app


def main() -> None:
    print(json.dumps(create_app().openapi(), indent=2))


if __name__ == "__main__":
    main()

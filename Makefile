dev:
	docker compose up --build

stop:
	docker compose down

test:
	docker compose run --rm api pytest

seed:
	docker compose run --rm api python -m app.seed

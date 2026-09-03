.PHONY: install dev test lint run once check render docker-build docker-up docker-logs

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests

run:
	python -m dealbot run

once:
	python -m dealbot once

check:
	python -m dealbot check

render:
	python -m dealbot render

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-logs:
	docker compose logs -f --tail=200

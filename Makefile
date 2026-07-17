# insider-intel dev environment. Every target is one obvious thing.
# CI calls these same targets — green locally means green in CI.
COMPOSE := docker compose
POSTGRES_USER ?= insider
POSTGRES_DB   ?= insider_intel

.PHONY: up down rebuild shell logs test lint fmt precommit db-shell db-reset clean build

up: ## build (cached) and start app + ui + postgres
	$(COMPOSE) up -d --build

down: ## stop the stack (keeps volumes)
	$(COMPOSE) down

build: ## build images without starting anything (used by CI)
	$(COMPOSE) build

rebuild: ## rebuild images from scratch and restart
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d

shell: ## bash inside the running app container
	$(COMPOSE) exec app bash

logs: ## follow logs from all services
	$(COMPOSE) logs -f --tail=100

test: ## run pytest inside the container
	$(COMPOSE) run --rm app pytest -q

lint: ## ruff lint inside the container
	$(COMPOSE) run --rm --no-deps app ruff check apps shared tests

fmt: ## ruff autoformat + safe lint fixes inside the container
	$(COMPOSE) run --rm --no-deps app sh -c "ruff format apps shared tests && ruff check --fix apps shared tests"

precommit: ## run all pre-commit hooks (incl. secrets scan) inside the container
	$(COMPOSE) run --rm --no-deps app pre-commit run --all-files

db-shell: ## psql into the sidecar
	$(COMPOSE) exec db psql -U $(POSTGRES_USER) $(POSTGRES_DB)

db-reset: ## nuke ONLY the Postgres volume and restart a fresh DB
	$(COMPOSE) rm -sf db
	docker volume rm -f insider-intel_pgdata
	$(COMPOSE) up -d --wait db

clean: ## full teardown: containers, volumes, local images, dangling layers
	$(COMPOSE) down -v --rmi local --remove-orphans
	docker image prune -f
	# compose abandons anonymous volumes when it recreates containers;
	# prune only touches unused ANONYMOUS volumes (named ones need --all)
	docker volume prune -f

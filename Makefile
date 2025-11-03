COMPOSE ?= docker compose
ENV_FILE ?= .env

.PHONY: up down logs build

up:
	$(COMPOSE) --env-file $(ENV_FILE) up --build

down:
	$(COMPOSE) --env-file $(ENV_FILE) down

logs:
	$(COMPOSE) --env-file $(ENV_FILE) logs -f

build:
	$(COMPOSE) --env-file $(ENV_FILE) build

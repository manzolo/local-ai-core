# local-ai-core — the everyday commands. `make help` lists them.
COMPOSE      ?= docker compose
FAMILY       := -f docker-compose.yml -f docker-compose.local-ai.yml
URL          ?= http://localhost:8090
PYTEST       ?= pytest

.PHONY: help up up-family down logs ps test lint mutations collaudo screenshots grep-private

help:          ## this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-13s %s\n", $$1, $$2}'

up:            ## start standalone (own Ollama)
	$(COMPOSE) up -d --build

up-family:     ## start on local-ai-net with the family's Ollama
	$(COMPOSE) $(FAMILY) up -d --build

down:          ## stop (data and models stay in the volumes)
	$(COMPOSE) $(FAMILY) down

logs:          ## follow the logs
	$(COMPOSE) logs -f --tail 50

ps:            ## what is running
	$(COMPOSE) ps

test:          ## CPU tests with fake Ollama (what CI runs)
	$(PYTEST) -q

lint:          ## ruff
	ruff check .

mutations:     ## break the code on purpose: every mutation must make the tests fail
	scripts/mutations.sh $(PYTEST)

collaudo:      ## the real test on the GPU, against the running stack
	scripts/collaudo.sh $(URL)

screenshots:   ## README screenshots through the web UI (example notes only!)
	scripts/screenshots.sh $(URL)

grep-private:  ## nothing private may reach the public repo
	scripts/grep-private.sh

.PHONY: dev worker worker-once test qa lint typecheck static-check migrate migration-check \
        doctor smoke secrets up up-prod observability down logs backup restore \
        recovery-backup recovery-verify recovery-drill

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

worker:
	python -m app.worker

worker-once:
	python -m app.worker --once

test:
	python -m pytest --cov=app --cov-report=term-missing

qa:
	./scripts/qa_release.sh

lint:
	ruff check app tests scripts

typecheck:
	mypy app

static-check:
	python -m compileall -q app migrations scripts tests
	node --check app/static/app.js

migrate:
	alembic upgrade head

migration-check:
	alembic check

doctor:
	python scripts/doctor.py

smoke:
	python scripts/smoke_test.py

secrets:
	python scripts/generate_secrets.py

up:
	docker compose up -d --build

up-prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

observability:
	docker compose --profile observability up -d prometheus grafana

down:
	docker compose --profile observability down

logs:
	docker compose logs -f api worker migrate nginx

backup:
	./scripts/backup.sh

restore:
	@test -n "$(ARTIFACT)" -a -n "$(RECEIPT)" || (echo "Use: make restore ARTIFACT=/path/backup RECEIPT=/path/receipt" && exit 1)
	./scripts/restore.sh "$(ARTIFACT)" "$(RECEIPT)"

recovery-backup:
	python scripts/recovery_backup.py $(RECOVERY_ARGS)

recovery-verify:
	@test -n "$(ARTIFACT)" -a -n "$(RECEIPT)" || (echo "Use: make recovery-verify ARTIFACT=/path/backup RECEIPT=/path/receipt" && exit 1)
	python scripts/recovery_verify.py "$(ARTIFACT)" "$(RECEIPT)" $(RECOVERY_ARGS)

recovery-drill:
	@test -n "$(ARTIFACT)" -a -n "$(RECEIPT)" || (echo "Use: make recovery-drill ARTIFACT=/path/backup RECEIPT=/path/receipt" && exit 1)
	python scripts/recovery_restore_drill.py "$(ARTIFACT)" "$(RECEIPT)" $(RECOVERY_ARGS)

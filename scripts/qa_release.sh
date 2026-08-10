#!/usr/bin/env sh
set -eu

# Only one full release-QA may run against a workspace at a time. Concurrent
# coverage, SQLite and recovery processes would compete for shared resources
# and could produce non-deterministic release evidence.
if command -v flock >/dev/null 2>&1; then
  QA_LOCK_FILE=${QA_LOCK_FILE:-/tmp/teleflow-release-qa.lock}
  exec 9>"$QA_LOCK_FILE"
  if ! flock -n 9; then
    echo "Another TeleFlow release-QA process already holds $QA_LOCK_FILE" >&2
    exit 73
  fi
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
PORT=${QA_PORT:-18080}
API_PID=
TEST_PIDS=
stop_test_groups() {
  for pid in $TEST_PIDS; do
    /bin/kill -TERM -- "-$pid" 2>/dev/null || /bin/kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 0.2
  for pid in $TEST_PIDS; do
    if /bin/kill -0 "$pid" 2>/dev/null; then
      /bin/kill -KILL -- "-$pid" 2>/dev/null || /bin/kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  TEST_PIDS=
}
stop_api() {
  if [ -z "$API_PID" ]; then
    return
  fi
  # Uvicorn and any telemetry/helper children run in their own process group.
  /bin/kill -TERM -- "-$API_PID" 2>/dev/null || true
  attempts=0
  while /bin/kill -0 -- "-$API_PID" 2>/dev/null && [ "$attempts" -lt 50 ]; do
    sleep 0.1
    attempts=$((attempts + 1))
  done
  if /bin/kill -0 -- "-$API_PID" 2>/dev/null; then
    /bin/kill -KILL -- "-$API_PID" 2>/dev/null || true
  fi
  wait "$API_PID" 2>/dev/null || true
  API_PID=
}
cleanup() {
  stop_test_groups
  stop_api
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT"

# Validate the pristine release set before compileall/pytest create temporary
# bytecode in a clean unpacked archive that has no Git ignore metadata.
python scripts/validate_release_assets.py --json >"$TMP/release-assets.json"

# Unit/integration tests construct isolated settings and databases themselves.
# Run them before exporting the release-smoke environment so no TELEFLOW_*
# variable can accidentally override a fixture through pydantic-settings.
python -m compileall -q app migrations scripts tests
python scripts/check_function_docs.py
python scripts/generate_manifest.py --check
node --check app/static/app.js
node --check app/static/sw.js
for shell_script in scripts/*.sh; do
  sh -n "$shell_script"
done
if command -v nginx >/dev/null 2>&1 \
  && command -v openssl >/dev/null 2>&1 \
  && command -v systemd-analyze >/dev/null 2>&1; then
  sh scripts/validate_infrastructure.sh
else
  echo "Infrastructure syntax tools unavailable; nginx/systemd validation skipped"
fi

# Execute every test module with ordinary pytest semantics first, including
# fixture teardown and session finalizers. One module per process prevents a
# leaked TestClient/SQLite resource from contaminating another module.
for test_file in tests/test_*.py; do
  echo "Functional regression: $test_file"
  timeout --kill-after=10s "${QA_FUNCTIONAL_FILE_TIMEOUT_SECONDS:-180}s" \
    python scripts/run_pytest.py -q "$test_file"
done

# Gather statement coverage in twenty isolated processes. The full
# functional regression above is authoritative for teardown behavior; this
# second pass exits after each test's setup/call result to avoid a known
# instrumented TestClient/SQLite teardown deadlock in some managed Python 3.13
# runtimes. Larger publisher and pilot groups are intentionally split because
# their aggregate runtime may exceed conservative container watchdog limits.
COVERAGE_BASE="$TMP/.coverage"
export COVERAGE_FILE="$COVERAGE_BASE"
# Python 3.12+ exposes sys.monitoring.  Coverage's sysmon core avoids a known
# trace-hook interaction with AnyIO/TestClient teardown in instrumented
# container runtimes while preserving statement coverage data.
export COVERAGE_CORE=${COVERAGE_CORE:-sysmon}
python -m coverage erase
run_test_group() {
  name=$1
  shift
  if COVERAGE_FILE="$COVERAGE_BASE.$name" \
      timeout --kill-after=10s "${QA_TEST_SHARD_TIMEOUT_SECONDS:-300}s" \
      python scripts/run_pytest_coverage.py -q "$@"; then
    echo "Coverage shard $name passed"
  else
    status=$?
    echo "Coverage shard $name failed or exceeded QA_TEST_SHARD_TIMEOUT_SECONDS" >&2
    return "$status"
  fi
}

run_test_group core \
  tests/test_analytics_sessions.py \
  tests/test_antivirus.py \
  tests/test_api_workflow.py \
  tests/test_ai_service_unit.py \
  tests/test_auth_security.py \
  tests/test_automation_admin_api.py \
  tests/test_automation_api_edges.py \
  tests/test_automation_flows.py \
  tests/test_flow_engine_edges.py \
  tests/test_business_admin_api.py \
  tests/test_business_automation.py \
  tests/test_business_client.py \
  tests/test_connection_admin_api.py \
  tests/test_conversation_api.py \
  tests/test_external_api.py \
  tests/test_inbound_edge_cases.py \
  tests/test_inbound_lifecycle_edges.py \
  tests/test_job_api_edges.py \
  tests/test_outbox_edge_cases.py \
  tests/test_privacy_api_edges.py \
  tests/test_privacy_service_edges.py \
  tests/test_user_admin_api.py
run_test_group publisher1 \
  tests/test_campaign_api_edges.py \
  tests/test_campaign_variants.py \
  tests/test_config_migrations.py \
  tests/test_scheduler_service_edges.py \
  tests/test_scheduler_occurrences.py
run_test_group publisher2 \
  tests/test_crypto_totp.py \
  tests/test_delivery_safety.py \
  tests/test_delivery_service_edges.py \
  tests/test_safety_edges.py
run_test_group publisher3 \
  tests/test_destination_api_edges.py \
  tests/test_destination_bulk.py \
  tests/test_destination_bulk_edges.py
run_test_group publisher4 \
  tests/test_destination_windows.py
run_test_group finalhardening \
  tests/test_final_hardening.py
run_test_group governance \
  tests/test_governance_resilience.py \
  tests/test_key_rotation_edges.py
run_test_group changes1 \
  tests/test_change_management.py \
  tests/test_release_trust.py
run_test_group changes2 \
  tests/test_supply_chain.py \
  tests/test_supply_chain_edges.py
run_test_group controlled \
  tests/test_controlled_operations.py
run_test_group pilot1 \
  tests/test_pilot_readiness.py
run_test_group pilot2 \
  tests/test_pilot_certification.py \
  tests/test_pilot_canary_errors.py
run_test_group commissioning \
  tests/test_commissioning_edges.py \
  tests/test_commissioning_portability.py \
  tests/test_config_bundle_security.py \
  tests/test_config_bundle_edges.py
run_test_group artifact \
  tests/test_artifact_trust.py \
  tests/test_artifact_verifier_edges.py
run_test_group recovery \
  tests/test_recovery_assurance.py \
  tests/test_recovery_api_admin.py
run_test_group security1 \
  tests/test_hardening.py \
  tests/test_multitenancy_integrations.py \
  tests/test_security_runtime_edges.py
run_test_group security2 \
  tests/test_distributed_locks.py \
  tests/test_logging_and_factory.py \
  tests/test_mtproto_adapter.py \
  tests/test_pwa.py \
  tests/test_rbac_media.py \
  tests/test_storage_s3.py \
  tests/test_transport_adapters.py \
  tests/test_worker_orchestration.py
run_test_group operations \
  tests/test_operations_slo.py
run_test_group capacity \
  tests/test_capacity_backpressure.py
run_test_group execution \
  tests/test_execution_fencing.py
run_test_group continuity \
  tests/test_continuity_assurance.py \
  tests/test_continuity_api_edges.py \
  tests/test_function_documentation.py

# Coverage instrumentation can block while appending to an existing data file
# in some container runtimes. Each process writes an independent file and
# coverage.py combines them after all test processes have exited.
python -m coverage combine "$TMP"
COVERAGE_LOG="$TMP/coverage.log"
if ! python -m coverage report --precision=2 --fail-under=80 >"$COVERAGE_LOG" 2>&1; then
  cat "$COVERAGE_LOG"
  exit 1
fi
cat "$COVERAGE_LOG"
TEST_COUNT=$(PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --collect-only -q 2>/dev/null | awk -F': ' '/^tests\// {sum += $2} END {print sum}')
echo "${TEST_COUNT:-unknown} automated tests passed across 21 coverage processes"

export TELEFLOW_ENVIRONMENT=test
export TELEFLOW_DEBUG=true
export TELEFLOW_DATABASE_URL="sqlite:///$TMP/qa.db"
export TELEFLOW_AUTO_CREATE_SCHEMA=false
export TELEFLOW_MASTER_KEY='qa-master-key-with-sufficient-entropy-0123456789'
export TELEFLOW_JWT_SECRET='qa-jwt-secret-with-sufficient-entropy-0123456789'
export TELEFLOW_BOOTSTRAP_ADMIN_EMAIL='qa-owner@example.com'
export TELEFLOW_BOOTSTRAP_ADMIN_PASSWORD='QA-Owner-Password_123!'
export TELEFLOW_BOOTSTRAP_ADMIN_NAME='QA Owner'
export TELEFLOW_PUBLIC_BASE_URL="http://127.0.0.1:$PORT"
export TELEFLOW_CORS_ORIGINS="http://127.0.0.1:$PORT"
export TELEFLOW_ALLOWED_HOSTS="127.0.0.1,localhost"
export TELEFLOW_STORAGE_PATH="$TMP/storage"
export TELEFLOW_TELEGRAM_FAKE_MODE=true
export TELEFLOW_METRICS_ENABLED=true

MIGRATION_ROUNDTRIP_BASE=${QA_MIGRATION_ROUNDTRIP_BASE:-ac4e6f8b2d5f}
python scripts/run_command.py --timeout "${QA_MIGRATION_TIMEOUT_SECONDS:-60}" --kill-after 5 -- alembic upgrade head
python scripts/run_command.py --timeout "${QA_MIGRATION_CHECK_TIMEOUT_SECONDS:-30}" --kill-after 5 -- alembic check
# Release 2.5 must be safely reversible to the 2.4 head before being applied again.
python scripts/run_command.py --timeout "${QA_MIGRATION_TIMEOUT_SECONDS:-60}" --kill-after 5 -- alembic downgrade "$MIGRATION_ROUNDTRIP_BASE"
python scripts/run_command.py --timeout "${QA_MIGRATION_TIMEOUT_SECONDS:-60}" --kill-after 5 -- alembic upgrade head
python scripts/run_command.py --timeout "${QA_MIGRATION_CHECK_TIMEOUT_SECONDS:-30}" --kill-after 5 -- alembic check
python scripts/run_command.py --timeout "${QA_DOCTOR_TIMEOUT_SECONDS:-60}" --kill-after 5 -- python scripts/doctor.py --json >"$TMP/doctor.json"

setsid python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$TMP/uvicorn.log" 2>&1 &
API_PID=$!
python - "$PORT" <<'PY'
import sys
import time
import urllib.request
port = sys.argv[1]
url = f"http://127.0.0.1:{port}/api/v1/health/live"
for _ in range(100):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit("API did not become healthy")
PY
python scripts/smoke_test.py \
  --base-url "http://127.0.0.1:$PORT" \
  --email "$TELEFLOW_BOOTSTRAP_ADMIN_EMAIL" \
  --password "$TELEFLOW_BOOTSTRAP_ADMIN_PASSWORD"

# The SQLite release-smoke uses one writer at a time. Stop the API before the
# worker cycle so the archive check is deterministic on slow/network filesystems.
stop_api
# Give the OS a brief grace period to release SQLite file locks and sockets.
sleep 1

# Governance/resilience acceptance: every newly written audit event must form a
# valid chain, and the master-key rotation preflight must decrypt the entire
# current secret inventory without mutating it.
python scripts/run_command.py --timeout "${QA_AUDIT_TIMEOUT_SECONDS:-30}" --kill-after 5 -- \
  python scripts/audit_chain.py verify --include-system --json >"$TMP/audit-chain.json"
export TELEFLOW_NEW_MASTER_KEY='qa-new-master-key-with-sufficient-entropy-9876543210'
python scripts/run_command.py --timeout "${QA_KEY_ROTATION_TIMEOUT_SECONDS:-30}" --kill-after 5 -- \
  python scripts/rotate_master_key.py --dry-run --json >"$TMP/key-rotation.json"
unset TELEFLOW_NEW_MASTER_KEY

python scripts/run_command.py --timeout "${QA_WORKER_TIMEOUT_SECONDS:-60}" --kill-after 10 -- python -m app.worker --once
python scripts/run_command.py --timeout "${QA_RECOVERY_TIMEOUT_SECONDS:-120}" --kill-after 10 -- \
  python scripts/recovery_smoke.py --output-dir "$TMP/recovery-smoke" --json >"$TMP/recovery-smoke.json"

echo "TeleFlow release QA passed"

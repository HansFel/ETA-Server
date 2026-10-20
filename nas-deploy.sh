#!/usr/bin/env bash
# Run on the NAS, in the existing ETA project. Never creates/deletes data volumes.
set -Eeuo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

compose_file=compose.yaml
project="${COMPOSE_PROJECT_NAME:-}"
image=eta-heiz-kontroller:archive-20261015
check_only=0
include_rights=0
usage() {
    cat <<'HELP'
Usage: bash nas-deploy.sh [--check] [--include-rights-update] [--project NAME] [--compose FILE] [--image IMAGE]

--check         Read-only checks; no backup, migration or service restart.
--include-rights-update  Also migrate old roles to per-person permissions and direct switching.
--project NAME  Existing Docker Compose project name (must match the running app).
--compose FILE Existing instance configuration (default: compose.yaml).
--image IMAGE  Already loaded, tested image (default: eta-heiz-kontroller:archive-20261015).

Without --include-rights-update the old instance must already have migration 0002.
After deployment always include compose.deployed.yaml when using Compose.
HELP
}
while (($#)); do
    case "$1" in
        --check) check_only=1; shift ;;
        --include-rights-update) include_rights=1; shift ;;
        --project|--compose|--image)
            (($# >= 2)) || { usage; exit 2; }
            case "$1" in
                --project) project=$2 ;; --compose) compose_file=$2 ;; --image) image=$2 ;;
            esac
            shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }
command -v docker >/dev/null || fail 'Docker is not available.'
[[ -f "$compose_file" ]] || fail "Missing instance configuration: $compose_file"
[[ -f scripts/pre_archive_update_backup.py ]] || fail 'Missing backup helper.'
base=(docker compose)
[[ -z "$project" ]] || base+=(-p "$project")
base+=(-f "$compose_file")
current=("${base[@]}")
[[ ! -f compose.deployed.yaml ]] || current+=(-f compose.deployed.yaml)
"${current[@]}" config --quiet
image_id=$(docker image inspect --format '{{.Id}}' "$image") || fail 'Load the tested image onto the NAS first.'
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Unexpected image identity.'
for service in web worker; do
    container=$("${current[@]}" ps -q "$service")
    [[ -n "$container" && "$container" != *$'\n'* ]] || fail "Exactly one running $service container is required. Check the project name."
    [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] || fail "$service is not running."
done
# Prevent accidentally shipping the separate rights migration to an older NAS.
"${current[@]}" exec -T worker python manage.py shell -c \
    "from django.db.migrations.recorder import MigrationRecorder; names=set(MigrationRecorder.Migration.objects.filter(app='heating').values_list('name',flat=True)); assert '0001_initial' in names, 'ETA-Basisschema fehlt'; assert '0002_person_grants' in names or $include_rights, 'Personenrechte-Migration 0002 fehlt: --include-rights-update nur fuer das freigegebene Gesamtupdate verwenden'; print('Personenrechte werden mit aktualisiert.' if '0002_person_grants' not in names else 'Personenrechte bereits aktuell.')"
# Existing NAS installations may have kept backups in the container filesystem.
# Such backups must be exported BEFORE container replacement and mounted persistently.
backup_container=$("${current[@]}" ps -q worker)
backup_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/backups"}}{{.Source}}{{end}}{{end}}' "$backup_container")
backup_path=$("${current[@]}" exec -T worker python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); from django.conf import settings; print(settings.BACKUP_DIR)")
[[ "$backup_path" == /backups ]] || fail 'This script requires BACKUP_DIR=/backups; adapt the persistent backup mount first.'
if [[ -n "$backup_mount" && "$(readlink -f "$backup_mount")" != "$(pwd -P)/backups" ]]; then
    fail 'Existing backup mount differs from the project backups directory. Preserve that mount explicitly before deployment.'
fi
docker run --rm --network none -e SECRET_KEY=deployment-preflight-only -e DATA_DIR=/tmp/eta-check \
    "$image_id" python -c \
    "import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; import django; django.setup(); from heating.archives import compact_batch; from importlib import import_module; import_module('heating.migrations.0003_sample_archive'); print('Archiv-Image bereit.')"
echo "Preflight successful. Target: $image ($image_id)"
((check_only == 0)) || exit 0

# A lock prevents two deployments of this instance from overlapping.
mkdir .nas-deploy.lock 2>/dev/null || fail 'Another deployment holds .nas-deploy.lock. Check it before removing a stale lock.'
downtime=0
success=0
rollback=''
cleanup() {
    local status=$?
    trap - EXIT
    if ((success == 0 && downtime == 1)); then
        # No automatic DB rollback: that could discard measurements or archive data.
        "${current[@]}" stop web worker >/dev/null 2>&1 || true
        echo "Deployment failed; web and worker are stopped. Do not start an old image against an archived DB." >&2
        echo "Configuration and previous image IDs: $rollback" >&2
        echo 'The checked database backup is in the backup directory printed above. No volumes were deleted.' >&2
    fi
    rmdir .nas-deploy.lock 2>/dev/null || true
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

rollback="backups/deploy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$rollback"
cp -- "$compose_file" "$rollback/compose.before.yaml"
[[ ! -f compose.deployed.yaml ]] || cp compose.deployed.yaml "$rollback/compose.deployed.before.yaml"
[[ ! -f .env ]] || cp .env "$rollback/env.before"
for service in web worker; do
    container=$("${current[@]}" ps -q "$service")
    docker inspect --format '{{.Name}} {{.Image}}' "$container" >> "$rollback/images.before.txt"
done

echo 'Creating and restoring an isolated pre-update backup with the OLD application...'
"${current[@]}" exec -T worker python - < scripts/pre_archive_update_backup.py
if [[ -z "$backup_mount" ]]; then
    echo 'Exporting existing container backups into the persistent project backups directory...'
    docker cp -a "$backup_container:/backups/." "$(pwd -P)/backups/"
fi
docker run --rm --network none --user 0 --mount "type=bind,source=$(pwd -P)/backups,target=/backup-check" \
    "$image_id" chown eta:eta /backup-check
docker run --rm -i --network none --mount "type=bind,source=$(pwd -P)/backups,target=/backup-check,readonly" \
    "$image_id" python - <<'PY'
import pathlib, json, hashlib
root = pathlib.Path('/backup-check')
manifests = list(root.glob('*.sqlite3.json'))
assert manifests, 'Keine kopierte Sicherung gefunden'
for manifest in manifests:
    meta = json.loads(manifest.read_text())
    path = root / meta['filename']
    assert path.resolve().parent == root and not path.is_symlink(), 'Ungueltiger Sicherungspfad'
    with path.open('rb') as handle:
        assert hashlib.file_digest(handle, 'sha256').hexdigest() == meta['sha256'], 'Backup-Pruefsumme falsch'
print('Persistente Sicherungen geprueft:', len(manifests))
PY

# Pin the immutable image ID; never build or pull an unexpected image during rollout.
candidate="$rollback/compose.candidate.yaml"
printf 'services:\n  init:\n    image: %s\n    volumes:\n      - ./backups:/backups\n  web:\n    image: %s\n    volumes:\n      - ./backups:/backups\n  worker:\n    image: %s\n    volumes:\n      - ./backups:/backups\n' \
    "$image_id" "$image_id" "$image_id" > "$candidate"
target=("${current[@]}" -f "$candidate")
"${target[@]}" config --quiet
downtime=1
"${current[@]}" stop web worker
echo 'Running migrations, bootstrap and static file preparation...'
# Kein --no-build/--pull bei run: aeltere Compose-Versionen (NAS) kennen das nicht. Das Image ist per
# ID festgelegt und lokal vorhanden; Compose baut oder laedt es daher nicht.
"${target[@]}" run --rm --no-deps init
started_at=$(date -u +%s)
"${target[@]}" up -d --no-deps --no-build web worker

echo 'Waiting for web and a new worker heartbeat (up to 180 seconds)...'
healthy=0
for ((attempt=0; attempt<36; attempt++)); do
    if "${target[@]}" exec -T web python -c \
        "import os, urllib.request; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); from django.conf import settings; prefix=getattr(settings,'SCRIPT_NAME','').rstrip('/'); urllib.request.urlopen('http://127.0.0.1:8000'+prefix+'/health/', timeout=5)" >/dev/null 2>&1 \
        && "${target[@]}" exec -T worker python manage.py shell -c \
        "from heating.services import get_state; from django.utils.dateparse import parse_datetime; from django.utils import timezone; s=get_state('worker'); at=parse_datetime(s['at']); assert s.get('ok') and at.timestamp()>=$started_at and (timezone.now()-at).total_seconds()<30" >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 5
done
((healthy == 1)) || fail 'Health checks did not pass.'
"${target[@]}" exec -T worker python manage.py migrate --check
"${target[@]}" exec -T worker python manage.py shell -c \
    "from heating.archives import storage_stats; from heating.services import get_state; print('Storage:', storage_stats()); print('Logger:', get_state('logger'))"

# Persist the override only after success. Config backups remain mode 600/700.
cp "$candidate" compose.deployed.yaml.tmp
mv compose.deployed.yaml.tmp compose.deployed.yaml
success=1
echo 'Deployment completed. The services use the checked archive image.'
echo "For future Compose commands use: -f $compose_file -f compose.deployed.yaml (and the same project name)."
echo "Protected deployment record: $rollback"

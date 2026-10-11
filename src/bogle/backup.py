"""Backup and restore of the bogle database (issues #32 and #33).

Both run the PostgreSQL client programs as subprocesses, never SQL through
psycopg: a plain dump from ``pg_dump`` >= 17.6/18 opens with a ``\\restrict``
meta-command and carries its rows as ``COPY ... FROM stdin``, and only ``psql``
reads either. So this module finds ``pg_dump``/``pg_restore``/``psql``, runs them
against the database ``BOGLE_DATABASE_URL`` points at, and checks what came out.

**What a backup carries** is the whole database, two schemas: ``public`` (the
ledger, the settings and ``price_history`` — which looks like a cache and is not
one: brapi revalidates only the last 30 sessions and Yahoo skips some, so a table
loaded again would differ from the one brapi corrected) and ``migrations``
(yoyo's bookkeeping, without which every migration looks pending and the next
start tries to create tables that exist). ``~/.cache/bogle`` and ``.env`` stay
out, and the README says so.

**A restore replaces, it never merges.** Both schemas are dropped and the dump
replayed in one transaction, which commits only if the result is a bogle database
this version of the code knows. ``pg_dump --clean`` alone would not do: it drops
what the *dump* has, so an older dump over a newer database left the newer tables
behind, and the next migration collided with them. Whatever goes wrong — a corrupt
file, a dump from a newer bogle, a file that is not a bogle backup, another
session holding a lock — the transaction rolls back and the database is exactly
as it was. The safety backup taken before is for the one case that check cannot
see: restoring the wrong (valid) file on purpose.

The connection is ``BOGLE_DATABASE_URL`` with its password taken out: the
password travels as ``PGPASSWORD`` in the subprocess's environment, never on its
command line (where ``ps`` shows it) and never in a message.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from glob import glob
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import IsolationLevel, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.db import DEFAULT_TIMEZONE, get_connection, get_database_url, known_migrations, migrate_if_pending
from bogle.domain.errors import BackupError

TOOLS_ENV = "BOGLE_PG_BIN"
"""Directory with the PostgreSQL client programs, when PATH is not the answer."""

KNOWN_DIRS: tuple[str, ...] = (
    "/opt/homebrew/opt/postgresql@*/bin",
    "/opt/homebrew/opt/libpq/bin",
    "/usr/local/opt/postgresql@*/bin",
    "/usr/local/opt/libpq/bin",
    "/Applications/Postgres.app/Contents/Versions/*/bin",
    "/usr/lib/postgresql/*/bin",
    "/usr/pgsql-*/bin",
)
"""Where the client lives when it is installed but not on the PATH.

``brew install postgresql@18`` — what the README says — is keg-only: the
programs stay under ``/opt/homebrew/opt/postgresql@18/bin`` until ``brew link``.
Debian keeps each major under ``/usr/lib/postgresql/<major>/bin`` behind a
wrapper. Looking there turns "installed, just not on the PATH" from an error
into a backup."""

TABLES: tuple[tuple[str, str, str], ...] = (
    ("assets", "ativo", "ativos"),
    ("transactions", "transação", "transações"),
    ("user_settings", "configuração", "configurações"),
    ("price_history", "fechamento", "fechamentos"),
)
"""The tables a backup reports, with the words for one and for many."""

_SCHEMA_TABLES = ("public.assets", "public.transactions")
"""What makes a database a bogle database, as far as a backup is concerned."""

_DUMP_COMPLETE = b"PostgreSQL database dump complete"
_DUMP_HEADER = b"PostgreSQL database dump"
_CUSTOM_MAGIC = b"PGDMP"
_VERSION = re.compile(r"\(PostgreSQL\)\s+(\d+)")
_ARCHIVE_CREATED = re.compile(r"Archive created at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

_NOT_A_BACKUP = "bogle-not-a-backup"
_NEWER_DUMP = "bogle-newer-dump"

_PREPARE = """
SET client_min_messages = warning;
SET lock_timeout = '5s';
DROP SCHEMA IF EXISTS migrations CASCADE;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
"""
"""Runs first, inside the restore's transaction: the database is emptied, and the
dump fills it. ``lock_timeout`` because a session holding a lock (a worker of a
TUI left open) would otherwise make the restore wait forever."""


class DumpFormat(StrEnum):
    """``pg_dump --format``: ``custom`` is compressed and checkable with
    ``pg_restore --list``; ``plain`` is SQL a person can read."""

    CUSTOM = "custom"
    PLAIN = "plain"

    @property
    def extension(self) -> str:
        return "dump" if self is DumpFormat.CUSTOM else "sql"


def default_backup_dir() -> Path:
    """``$XDG_DATA_HOME/bogle/backups`` (``~/.local/share/bogle/backups``).

    Out of the current directory on purpose: that is usually the repository, which
    is public, and ``.gitignore`` cannot ignore ``*.sql`` (the migrations are SQL).
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / "bogle" / "backups"


def describe_rows(rows: Mapping[str, int]) -> str:
    """``5 ativos, 20 transações, 4 configurações, 773 fechamentos`` — the tables
    the database has, in a fixed order."""
    return ", ".join(_counted(rows))


def _counted(rows: Mapping[str, int]) -> list[str]:
    return [
        f"{fmt.count(rows[table])} {one if rows[table] == 1 else many}" for table, one, many in TABLES if table in rows
    ]


# ------------------------------------------------------------------ conexao


@dataclass(frozen=True, slots=True)
class Target:
    """The database to dump or replace, as the client programs are told about it."""

    conninfo: str
    """Connection string without the password: what goes on the command line."""
    password: str | None

    def env(self) -> dict[str, str]:
        """The subprocess environment: the caller's, plus the password."""
        env = dict(os.environ)
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env


def target_of(database_url: str) -> Target:
    """Split the password out of ``database_url``."""
    params = conninfo_to_dict(database_url)
    password = params.pop("password", None)
    return Target(conninfo=make_conninfo("", **params), password=str(password) if password is not None else None)


def _place(conn: psycopg.Connection[DictRow]) -> str:
    """``bogle em localhost`` — which database, without anything secret."""
    info = conn.info
    host = "localhost" if not info.host or info.host.startswith("/") else info.host
    port = f":{info.port}" if info.port and info.port != 5432 else ""
    return f"{info.dbname} em {host}{port}"


def _server_major(conn: psycopg.Connection[DictRow]) -> int:
    return conn.info.server_version // 10000


def _exists(conn: psycopg.Connection[DictRow], qualified: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS found", (qualified,)).fetchone()
    return bool(row and row["found"])


def has_bogle_schema(conn: psycopg.Connection[DictRow]) -> bool:
    return all(_exists(conn, table) for table in _SCHEMA_TABLES)


def table_counts(conn: psycopg.Connection[DictRow]) -> dict[str, int]:
    """Rows in each table of :data:`TABLES` the database has (an old one may lack some)."""
    counts: dict[str, int] = {}
    for table, _one, _many in TABLES:
        if not _exists(conn, f"public.{table}"):
            continue
        row = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier("public", table))).fetchone()
        counts[table] = row["n"] if row else 0
    return counts


# --------------------------------------------------------------- programas


def tool_major(path: Path) -> int | None:
    """Major version a client program reports, or ``None`` when it does not answer."""
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True, check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION.search(result.stdout)
    return int(match.group(1)) if match else None


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _known_candidates(name: str) -> list[Path]:
    return [path for pattern in KNOWN_DIRS for path in map(Path, glob(f"{pattern}/{name}")) if _executable(path)]


def find_tool(name: str, *, server_major: int) -> Path:
    """The client program ``name``, of a major at least the server's.

    ``BOGLE_PG_BIN`` wins when set — an explicit choice is never overridden, and a
    wrong one is an error. Otherwise the PATH, then :data:`KNOWN_DIRS`, newest
    major first. A client older than the server is refused: ``pg_dump`` will not
    read a newer server, and an older ``psql`` does not understand ``\\restrict``.
    """
    explicit = os.environ.get(TOOLS_ENV)
    if explicit:
        path = Path(explicit).expanduser() / name
        if not _executable(path):
            raise BackupError(f"{name} não está em {TOOLS_ENV}={explicit}.")
        major = tool_major(path)
        if major is None or major < server_major:
            raise BackupError(_too_old(name, path, major, server_major))
        return path

    too_old: list[tuple[Path, int | None]] = []
    on_path = shutil.which(name)
    if on_path is not None:
        major = tool_major(Path(on_path))
        if major is not None and major >= server_major:
            return Path(on_path)
        too_old.append((Path(on_path), major))
    known = [(path, tool_major(path)) for path in _known_candidates(name)]
    for path, major in sorted(known, key=lambda item: item[1] or 0, reverse=True):
        if major is not None and major >= server_major:
            return path
        too_old.append((path, major))
    if too_old:
        path, major = max(too_old, key=lambda item: item[1] or 0)
        raise BackupError(_too_old(name, path, major, server_major))
    raise BackupError(
        f"{name} não encontrado. Instale o cliente do PostgreSQL {server_major} "
        f"(macOS: brew install postgresql@{server_major}; Debian/Ubuntu: apt install "
        f"postgresql-client-{server_major}) ou aponte {TOOLS_ENV} para o diretório que tem o {name}."
    )


def _too_old(name: str, path: Path, major: int | None, server_major: int) -> str:
    version = f"{name} {major}" if major is not None else name
    return (
        f"{version} ({path}) é mais antigo que o servidor (PostgreSQL {server_major}). "
        f"Instale o cliente {server_major} ou aponte {TOOLS_ENV} para ele."
    )


def _run(command: Sequence[str | Path], target: Target) -> subprocess.CompletedProcess[str]:
    """A client program, output captured as text, the password in its environment."""
    return subprocess.run(
        [str(part) for part in command], env=target.env(), capture_output=True, text=True, check=False
    )


_TOOL_PREFIX = re.compile(r"^(?:pg_dump|pg_restore|psql)(?::[^:]*:\d+)?: (?:error|fatal): ", re.IGNORECASE)


def _last_line(text: str) -> str:
    """The program's last word, without its ``pg_restore: error:`` preamble."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _TOOL_PREFIX.sub("", lines[-1]) if lines else ""


# ------------------------------------------------------------------- backup


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    format: DumpFormat
    size: int
    rows: dict[str, int]
    """Counted in the same snapshot ``pg_dump`` read: what the file holds."""
    database: str
    """``bogle em localhost``."""


def _now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _destination(output: str | Path | None, dump_format: DumpFormat, dbname: str, when: datetime, tag: str) -> Path:
    """Where the file goes: a fresh name in the default directory, in a directory
    the user gave, or exactly the file the user gave."""
    stem = "-".join(part for part in ("bogle", _UNSAFE_NAME.sub("_", dbname), tag) if part)
    name = f"{stem}-{when:%Y-%m-%d-%H-%M-%S}.{dump_format.extension}"
    if output is None:
        directory = default_backup_dir()
    else:
        given = Path(output).expanduser()
        if not (given.is_dir() or str(output).endswith(os.sep)):
            given.parent.mkdir(parents=True, exist_ok=True)
            return given
        directory = given
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / name


def create_backup(
    *,
    output: str | Path | None = None,
    dump_format: DumpFormat = DumpFormat.CUSTOM,
    database_url: str | None = None,
    tag: str = "",
    now: Callable[[], datetime] = _now,
) -> BackupResult:
    """Dump the database to a file nobody else can read, and check the file.

    The dump is written to ``<name>.tmp`` and only renamed once ``pg_dump``
    finished and the file checks out (``pg_restore --list`` for custom, the
    closing line for plain): a run killed halfway leaves no file under a name that
    claims to be a backup. The database is read as it is — nothing here migrates
    it (see ``cli._main``).
    """
    url = database_url or get_database_url()
    target = target_of(url)
    conn = get_connection(url)
    try:
        server_major = _server_major(conn)
        pg_dump = find_tool("pg_dump", server_major=server_major)
        pg_restore = find_tool("pg_restore", server_major=server_major) if dump_format is DumpFormat.CUSTOM else None
        database = _place(conn)
        dbname = conn.info.dbname
        # Um snapshot so para contar e para copiar: as contagens do relatorio sao
        # as do arquivo, mesmo com outra sessao gravando no meio do dump.
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.transaction():
            if not has_bogle_schema(conn):
                raise BackupError(f"o banco {database} não tem o schema do bogle: não há o que copiar.")
            rows = table_counts(conn)
            snapshot_row = conn.execute("SELECT pg_export_snapshot() AS id").fetchone()
            assert snapshot_row is not None  # SELECT de funcao sempre devolve uma linha
            try:
                path = _destination(output or None, dump_format, dbname, now(), tag)
            except OSError as exc:
                raise BackupError(f"não foi possível criar o destino do backup: {exc.strerror or exc}.") from None
            _dump(pg_dump, target, dump_format, snapshot_row["id"], path, pg_restore)
        return BackupResult(path=path, format=dump_format, size=path.stat().st_size, rows=rows, database=database)
    finally:
        conn.close()


def _dump(
    pg_dump: Path, target: Target, dump_format: DumpFormat, snapshot: str, path: Path, pg_restore: Path | None
) -> None:
    partial = path.with_name(f"{path.name}.tmp")
    command = [
        pg_dump,
        f"--format={dump_format.value}",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        f"--snapshot={snapshot}",
        f"--dbname={target.conninfo}",
    ]
    try:
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)  # um .tmp que sobrou de antes manteria o modo dele
            with os.fdopen(fd, "wb") as out:
                result = subprocess.run(
                    [str(part) for part in command], stdout=out, stderr=subprocess.PIPE, env=target.env(), check=False
                )
        except OSError as exc:
            raise BackupError(f"não foi possível gravar {path}: {exc.strerror or exc}.") from None
        if result.returncode != 0:
            detail = _last_line(result.stderr.decode(errors="replace"))
            raise BackupError(f"pg_dump falhou: {detail or f'código {result.returncode}'}.")
        _verify(partial, dump_format, pg_restore, target)
        partial.replace(path)
    finally:
        with suppress(FileNotFoundError):
            partial.unlink()


def _verify(path: Path, dump_format: DumpFormat, pg_restore: Path | None, target: Target) -> None:
    if dump_format is DumpFormat.CUSTOM:
        assert pg_restore is not None  # resolvido junto com o pg_dump
        check = _run([pg_restore, "--list", path], target)
        if check.returncode != 0:
            raise BackupError(f"o dump gravado não passou na verificação: {_last_line(check.stderr)}.")
    elif not _ends_complete(path):
        raise BackupError("o dump gravado está incompleto: falta a linha final do pg_dump.")


def _ends_complete(path: Path) -> bool:
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        file.seek(max(0, file.tell() - 4096))
        return _DUMP_COMPLETE in file.read()


# ------------------------------------------------------------------ restore


@dataclass(frozen=True, slots=True)
class DumpInfo:
    path: Path
    format: DumpFormat
    """From the first bytes, never from the extension."""
    size: int
    created_at: datetime | None
    """When ``pg_dump`` wrote it (custom archives say so), else the file's mtime."""


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """Everything the confirmation shows, checked before anything is written."""

    dump: DumpInfo
    database: str
    has_schema: bool
    """``False`` for a database freshly created: nothing to lose, no safety backup."""
    rows: dict[str, int]
    """What the database has now, and the restore replaces."""
    url: str
    psql: Path
    pg_restore: Path | None


@dataclass(frozen=True, slots=True)
class RestoreResult:
    rows: dict[str, int]
    applied: list[str]
    """Migrations applied after the dump, for a dump older than the code."""
    safety: BackupResult | None


def _sniff(path: Path) -> DumpFormat:
    try:
        with path.open("rb") as file:
            head = file.read(8192)
    except FileNotFoundError:
        raise BackupError(f"arquivo não encontrado: {path}.") from None
    except OSError as exc:
        raise BackupError(f"não foi possível ler {path}: {exc.strerror or exc}.") from None
    if head.startswith(_CUSTOM_MAGIC):
        return DumpFormat.CUSTOM
    if _DUMP_HEADER in head:
        if not _ends_complete(path):
            raise BackupError(f"{path} está incompleto: o dump foi interrompido antes do fim. Nada foi alterado.")
        return DumpFormat.PLAIN
    raise BackupError(f"{path} não é um backup do bogle: não é um dump do PostgreSQL.")


def plan_restore(path: str | Path, *, database_url: str | None = None) -> RestorePlan:
    """Read the file and the database, and find the programs — without writing.

    A custom archive is listed with ``pg_restore --list`` here, so a file that is
    not one (or whose table of contents is broken) is refused before the
    confirmation is even asked.
    """
    dump_path = Path(path).expanduser()
    dump_format = _sniff(dump_path)
    url = database_url or get_database_url()
    target = target_of(url)
    conn = get_connection(url)
    try:
        server_major = _server_major(conn)
        psql = find_tool("psql", server_major=server_major)
        pg_restore = find_tool("pg_restore", server_major=server_major) if dump_format is DumpFormat.CUSTOM else None
        has_schema = has_bogle_schema(conn)
        rows = table_counts(conn) if has_schema else {}
        database = _place(conn)
    finally:
        conn.close()
    created_at = _archive_created(dump_path, pg_restore, target) if pg_restore is not None else _mtime(dump_path)
    return RestorePlan(
        dump=DumpInfo(dump_path, dump_format, dump_path.stat().st_size, created_at),
        database=database,
        has_schema=has_schema,
        rows=rows,
        url=url,
        psql=psql,
        pg_restore=pg_restore,
    )


def _archive_created(path: Path, pg_restore: Path, target: Target) -> datetime | None:
    listing = _run([pg_restore, "--list", path], target)
    if listing.returncode != 0:
        raise BackupError(f"{path} não é um backup válido: {_last_line(listing.stderr)}. Nada foi alterado.")
    match = _ARCHIVE_CREATED.search(listing.stdout)
    return datetime.fromisoformat(match.group(1)) if match else None


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(DEFAULT_TIMEZONE))


def restore(plan: RestorePlan, *, safety_backup: bool = True, now: Callable[[], datetime] = _now) -> RestoreResult:
    """Replace the database with the dump, all or nothing; then migrate it forward.

    Order: the safety backup (only when there is something to save), the archive
    turned into SQL (custom only, into a private temp file), then one ``psql``
    transaction — empty the two schemas, replay the dump, check the result — and
    only after it commits, the migrations a dump older than the code is missing.
    """
    safety = (
        create_backup(database_url=plan.url, tag="pre-restore", now=now) if safety_backup and plan.has_schema else None
    )
    target = target_of(plan.url)
    script, scratch = plan.dump.path, None
    try:
        if plan.dump.format is DumpFormat.CUSTOM:
            script = scratch = _archive_to_sql(plan, target)
        _replay(plan, script, target)
    finally:
        if scratch is not None:
            with suppress(FileNotFoundError):
                scratch.unlink()
    try:
        applied = migrate_if_pending(plan.url)
    except psycopg.Error as exc:
        where = f" O backup de segurança está em {safety.path}." if safety is not None else ""
        raise BackupError(f"o backup foi restaurado, mas as migrações falharam: {exc}.{where}") from None
    conn = get_connection(plan.url)
    try:
        rows = table_counts(conn)
    finally:
        conn.close()
    return RestoreResult(rows=rows, applied=applied, safety=safety)


def _archive_to_sql(plan: RestorePlan, target: Target) -> Path:
    assert plan.pg_restore is not None  # resolvido pelo plano para um arquivo custom
    fd, name = tempfile.mkstemp(prefix="bogle-restore-", suffix=".sql")
    os.close(fd)
    script = Path(name)
    result = _run(
        [plan.pg_restore, "--no-owner", "--no-privileges", f"--file={script}", plan.dump.path],
        target,
    )
    if result.returncode != 0:
        script.unlink()
        raise BackupError(
            f"{plan.dump.path} está corrompido ou incompleto: {_last_line(result.stderr)}. Nada foi alterado."
        )
    return script


def _verification() -> str:
    """The last statement of the transaction: refuses what this code cannot run on.

    Missing tables mean the file was a dump of some other database. An applied
    migration this code does not ship means the dump came from a newer bogle — the
    code would run against a schema it does not know, and the first error would
    blame the wrong thing.
    """
    known = ", ".join("'" + migration.replace("'", "''") + "'" for migration in known_migrations())
    return f"""
DO $bogle$
DECLARE
    unknown text;
BEGIN
    IF to_regclass('public.assets') IS NULL
       OR to_regclass('public.transactions') IS NULL
       OR to_regclass('migrations.yoyo_migration') IS NULL THEN
        RAISE EXCEPTION '{_NOT_A_BACKUP}';
    END IF;
    SELECT string_agg(migration_id, ', ' ORDER BY migration_id) INTO unknown
    FROM migrations.yoyo_migration
    WHERE NOT (migration_id = ANY (ARRAY[{known}]::text[]));
    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION '{_NEWER_DUMP}: %', unknown;
    END IF;
END
$bogle$;
"""


def _replay(plan: RestorePlan, script: Path, target: Target) -> None:
    result = _run(
        [
            plan.psql,
            "--no-psqlrc",
            "--quiet",
            "--set=ON_ERROR_STOP=1",
            "--single-transaction",
            f"--dbname={target.conninfo}",
            "--command",
            _PREPARE,
            "--file",
            script,
            "--command",
            _verification(),
        ],
        target,
    )
    if result.returncode != 0:
        raise BackupError(_refusal(plan.dump.path, result.stderr, result.returncode))


def _refusal(path: Path, stderr: str, code: int) -> str:
    error = next((line.split("ERROR:", 1)[1].strip() for line in stderr.splitlines() if "ERROR:" in line), "")
    if error.startswith(_NOT_A_BACKUP):
        return f"{path} não é um backup do bogle: faltam as tabelas do bogle. Nada foi alterado."
    if error.startswith(_NEWER_DUMP):
        migrations = error.split(":", 1)[1].strip()
        return (
            f"{path} é de uma versão mais nova do bogle (migração {migrations}): "
            "atualize o bogle antes de restaurá-lo. Nada foi alterado."
        )
    if "lock timeout" in error:
        return (
            "outra sessão está usando o banco: feche o bogle nos outros terminais e tente de novo. Nada foi alterado."
        )
    return f"o restore falhou e nada foi alterado: {error or _last_line(stderr) or f'psql saiu com código {code}'}."

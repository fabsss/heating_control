CREATE TABLE IF NOT EXISTS entities(
  entity_id     TEXT PRIMARY KEY,
  domain        TEXT NOT NULL,                 -- heating|lighting|shared
  kind          TEXT NOT NULL,
  room          TEXT,
  tracked_attrs TEXT,                          -- JSON-Liste der gespeicherten Attribute
  min_delta     REAL,
  heartbeat_s   INTEGER,
  debounce_s    INTEGER,
  tracked_since TEXT,                          -- ISO-8601 UTC, ab hier liegen Daten vor
  active        INTEGER NOT NULL DEFAULT 1,
  tracked_until TEXT
);

CREATE TABLE IF NOT EXISTS room_map(
  room        TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  role        TEXT NOT NULL,
  valid_from  TEXT NOT NULL DEFAULT '',        -- '' = seit jeher
  valid_to    TEXT,                            -- NULL = noch gültig
  UNIQUE(entity_id, room, role, valid_from)
);

CREATE TABLE IF NOT EXISTS users(
  user_id       TEXT PRIMARY KEY,
  person_entity TEXT,
  name          TEXT
);

CREATE TABLE IF NOT EXISTS state_changes(
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  entity_id         TEXT NOT NULL,
  state             TEXT,
  attrs             TEXT NOT NULL DEFAULT '{}',
  attrs_hash        TEXT NOT NULL,
  context_id        TEXT,
  context_user_id   TEXT,
  context_parent_id TEXT,
  source            TEXT NOT NULL,
  UNIQUE(entity_id, ts_utc, state, attrs_hash)
);
CREATE INDEX IF NOT EXISTS idx_sc_entity_ts ON state_changes(entity_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_sc_user ON state_changes(context_user_id);

CREATE TABLE IF NOT EXISTS ingest_runs(
  id         INTEGER PRIMARY KEY,
  source     TEXT NOT NULL,
  from_ts    TEXT,
  to_ts      TEXT,
  rows       INTEGER NOT NULL,
  status     TEXT NOT NULL,
  started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_calls(
  id          INTEGER PRIMARY KEY,
  ts_utc      TEXT NOT NULL,
  domain      TEXT NOT NULL,
  service     TEXT NOT NULL,
  entity_ids  TEXT NOT NULL DEFAULT '[]',          -- JSON-Liste, leer = Ziel über Bereich/Gerät
  data        TEXT NOT NULL DEFAULT '{}',
  context_id  TEXT,
  user_id     TEXT,
  parent_id   TEXT,
  UNIQUE(ts_utc, domain, service, entity_ids, context_id)
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON service_calls(ts_utc);
CREATE INDEX IF NOT EXISTS idx_calls_ctx ON service_calls(context_id);

CREATE TABLE IF NOT EXISTS automation_runs(
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  automation_entity TEXT NOT NULL,
  name              TEXT,
  context_id        TEXT,
  UNIQUE(ts_utc, automation_entity, context_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_ctx ON automation_runs(context_id);

CREATE TABLE IF NOT EXISTS change_origin(
  state_change_id INTEGER PRIMARY KEY,
  origin          TEXT NOT NULL,                   -- benutzer|automation|geraet_unklar|unbekannt
  via             TEXT NOT NULL,                   -- kontext|zeit|keine
  actor           TEXT,                            -- Benutzerkennung bei "benutzer"
  automation      TEXT                             -- Entity-ID der Automation bei "automation"
);

CREATE TABLE IF NOT EXISTS capture_state(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

DROP VIEW IF EXISTS manual_actions;
CREATE VIEW manual_actions AS
WITH base AS (
  SELECT sc.id AS change_id, sc.ts_utc, sc.entity_id, e.domain,
         COALESCE((SELECT rm.room FROM room_map rm
                   WHERE rm.entity_id = sc.entity_id
                     AND (rm.valid_from = '' OR rm.valid_from <= sc.ts_utc)
                     AND (rm.valid_to IS NULL OR sc.ts_utc < rm.valid_to)
                   ORDER BY rm.valid_from DESC LIMIT 1), e.room) AS room,
         sc.state AS new_state,
         json_extract(sc.attrs, '$.temperature') AS new_temp,
         json_extract(sc.attrs, '$.preset_mode') AS new_preset,
         LAG(sc.state) OVER w AS old_state,
         LAG(json_extract(sc.attrs, '$.temperature')) OVER w AS old_temp,
         LAG(json_extract(sc.attrs, '$.preset_mode')) OVER w AS old_preset,
         sc.context_user_id, sc.context_parent_id,
         co.origin AS origin, co.actor AS origin_actor, co.automation AS origin_automation
  FROM state_changes sc
  JOIN entities e ON e.entity_id = sc.entity_id
  LEFT JOIN change_origin co ON co.state_change_id = sc.id
  WHERE e.kind = 'climate'
  WINDOW w AS (PARTITION BY sc.entity_id ORDER BY sc.ts_utc, sc.id)
), diff AS (
  SELECT *,
    CASE WHEN old_state NOT IN ('unavailable', 'unknown') AND new_state NOT IN ('unavailable', 'unknown') THEN
      TRIM(
        (CASE WHEN old_temp IS NOT new_temp THEN ',temperature' ELSE '' END) ||
        (CASE WHEN old_preset IS NOT new_preset THEN ',preset_mode' ELSE '' END) ||
        (CASE WHEN old_state IS NOT new_state THEN ',hvac_mode' ELSE '' END), ',')
    ELSE '' END AS changed
  FROM base
  WHERE old_state IS NOT NULL
)
SELECT change_id, ts_utc, domain, room, entity_id, changed AS changed_attrs,
       json_object('temperature', old_temp, 'preset_mode', old_preset, 'hvac_mode', old_state) AS old,
       json_object('temperature', new_temp, 'preset_mode', new_preset, 'hvac_mode', new_state) AS new,
       CASE origin
         WHEN 'benutzer' THEN 'manual'
         WHEN 'automation' THEN 'automation'
         WHEN 'geraet_unklar' THEN 'device'
         WHEN 'unbekannt' THEN 'unknown'
         ELSE CASE WHEN context_parent_id IS NOT NULL THEN 'automation'
                   WHEN context_user_id IS NOT NULL THEN 'manual'
                   ELSE 'unknown' END
       END AS actor_class,
       COALESCE(origin_actor, context_user_id) AS actor_user_id,
       origin_automation
FROM diff
WHERE changed <> '';

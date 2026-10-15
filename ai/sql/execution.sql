CREATE TABLE IF NOT EXISTS deployments(
  rule_id           TEXT PRIMARY KEY,
  candidate_id      INTEGER NOT NULL,
  room              TEXT NOT NULL,                -- area_id
  ha_automation     TEXT NOT NULL,
  config_id         TEXT NOT NULL,
  climate_entity    TEXT NOT NULL,
  state             TEXT NOT NULL,                -- schatten|aktiv|entfernt
  blueprint_version TEXT NOT NULL,
  description       TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  removed_at        TEXT
);

CREATE TABLE IF NOT EXISTS rule_events(
  id            INTEGER PRIMARY KEY,
  rule_id       TEXT NOT NULL,
  ts_utc        TEXT NOT NULL,
  mode          TEXT NOT NULL,                    -- aktiv|schatten
  result        TEXT NOT NULL,                    -- ausgefuehrt|nicht_ausgefuehrt
  reason        TEXT NOT NULL DEFAULT '',
  target        TEXT NOT NULL,                    -- JSON
  current_state TEXT NOT NULL,                    -- JSON
  payload       TEXT NOT NULL,
  UNIQUE(rule_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_rule_events_rule ON rule_events(rule_id, ts_utc);

CREATE TABLE IF NOT EXISTS rule_outcomes(
  rule_event_id INTEGER PRIMARY KEY,
  outcome       TEXT NOT NULL,                    -- treffer|fehlausloesung|neutral|bestaetigt|korrigiert|unterbrochen|ohne_wirkung|nicht_gewertet
  lage          TEXT,                             -- vorher|nachher
  abstand_min   REAL,
  window_start  TEXT NOT NULL,
  window_end    TEXT NOT NULL,
  deviation     TEXT
);

CREATE TABLE IF NOT EXISTS jobs(
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  params      TEXT NOT NULL DEFAULT '{}',
  state       TEXT NOT NULL,                      -- laeuft|ok|fehler
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  message     TEXT
);

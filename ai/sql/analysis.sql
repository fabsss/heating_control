CREATE TABLE IF NOT EXISTS analysis_runs(
  id            INTEGER PRIMARY KEY,
  mode          TEXT NOT NULL,                 -- weekly|clusters
  model         TEXT NOT NULL,
  started_at    TEXT NOT NULL,                 -- ISO-8601 UTC
  finished_at   TEXT,
  params        TEXT NOT NULL DEFAULT '{}',    -- JSON
  status        TEXT NOT NULL,                 -- laeuft|abgeschlossen|fehlgeschlagen
  error         TEXT,
  prompt_hash   TEXT,
  prompt_text   TEXT,                          -- pseudonymisiert
  response_text TEXT,
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  cost_usd      REAL
);

CREATE TABLE IF NOT EXISTS candidates(
  id              INTEGER PRIMARY KEY,
  run_id          INTEGER NOT NULL REFERENCES analysis_runs(id),
  room            TEXT NOT NULL,               -- area_id
  trigger_json    TEXT NOT NULL,
  conditions_json TEXT NOT NULL DEFAULT '[]',
  action_json     TEXT NOT NULL,
  hypothesis      TEXT,
  status          TEXT NOT NULL,               -- offen|bestanden|zu_wenig_belege|abgelehnt|nicht_pruefbar
  note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id);

CREATE TABLE IF NOT EXISTS backtest_results(
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  metric       TEXT NOT NULL,
  value        REAL NOT NULL,
  PRIMARY KEY(candidate_id, metric)
);

CREATE TABLE IF NOT EXISTS pseudonyms(
  alias     TEXT PRIMARY KEY,                  -- person_1, person_2, ...
  entity_id TEXT NOT NULL,
  kind      TEXT NOT NULL
);

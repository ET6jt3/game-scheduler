package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
)

type HelperInstance struct {
	ID                string         `json:"id"`
	HelperID          string         `json:"helper_id"`
	Name              string         `json:"name"`
	LocationMode      string         `json:"location_mode"`
	Executable        string         `json:"executable"`
	WorkingDir        string         `json:"working_dir"`
	ConfigDir         string         `json:"config_dir"`
	DataDir           string         `json:"data_dir"`
	RuntimePath       string         `json:"runtime_path"`
	Enabled           bool           `json:"enabled"`
	DiscoveryMetadata map[string]any `json:"discovery_metadata,omitempty"`
}

// The new tables are additive, transactional and idempotent. No legacy row is rewritten.
func (s *Store) migrateHelpers() error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = tx.Exec(`CREATE TABLE IF NOT EXISTS helper_instances (id TEXT PRIMARY KEY, body TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS helper_settings (id TEXT PRIMARY KEY, body TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS execution_diagnostics (execution_id INTEGER PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE, body TEXT NOT NULL);`)
	if err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) ListHelpers() ([]HelperInstance, error) {
	rows, err := s.db.Query(`SELECT body FROM helper_instances ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []HelperInstance{}
	for rows.Next() {
		var b string
		var h HelperInstance
		if err = rows.Scan(&b); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(b), &h); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}
func (s *Store) GetHelper(id string) (HelperInstance, error) {
	var b string
	var h HelperInstance
	err := s.db.QueryRow(`SELECT body FROM helper_instances WHERE id=?`, id).Scan(&b)
	if err == sql.ErrNoRows {
		return h, ErrNotFound
	}
	if err != nil {
		return h, err
	}
	err = json.Unmarshal([]byte(b), &h)
	return h, err
}
func (s *Store) SaveHelper(h HelperInstance, create bool) error {
	b, err := json.Marshal(h)
	if err != nil {
		return err
	}
	if create {
		_, err = s.db.Exec(`INSERT INTO helper_instances(id,body) VALUES(?,?)`, h.ID, string(b))
		return err
	}
	res, err := s.db.Exec(`UPDATE helper_instances SET body=? WHERE id=?`, string(b), h.ID)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}
func (s *Store) DeleteHelper(id string) error {
	// Only the registration is removed; no helper files or legacy games/tasks are deleted.
	res, err := s.db.Exec(`DELETE FROM helper_instances WHERE id=?`, id)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}
func (s *Store) HelperSetting(id string, target any) error {
	var b string
	err := s.db.QueryRow(`SELECT body FROM helper_settings WHERE id=?`, id).Scan(&b)
	if err == sql.ErrNoRows {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(b), target)
}
func (s *Store) SaveHelperSetting(id string, value any) error {
	b, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("settings: %w", err)
	}
	_, err = s.db.Exec(`INSERT INTO helper_settings(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body`, id, string(b))
	return err
}

func (s *Store) SaveExecutionDiagnostics(id int64, value any) error {
	b, e := json.Marshal(value)
	if e != nil {
		return e
	}
	_, e = s.db.Exec(`INSERT INTO execution_diagnostics(execution_id,body) VALUES(?,?) ON CONFLICT(execution_id) DO UPDATE SET body=excluded.body`, id, string(b))
	return e
}
func (s *Store) ExecutionDiagnostics(id int64) (json.RawMessage, error) {
	var b string
	e := s.db.QueryRow(`SELECT body FROM execution_diagnostics WHERE execution_id=?`, id).Scan(&b)
	if e == sql.ErrNoRows {
		return nil, ErrNotFound
	}
	return json.RawMessage(b), e
}

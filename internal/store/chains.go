package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

// Chain definitions and run snapshots are separate: editing tomorrow's order
// never rewrites the history or the progress of an existing occurrence.
type Chain struct {
	ID                        int64   `json:"id"`
	Name                      string  `json:"name"`
	Time                      string  `json:"time"`
	Zone                      string  `json:"zone"`
	Days                      []int   `json:"days"`
	TaskIDs                   []int64 `json:"task_ids"`
	Enabled                   bool    `json:"enabled"`
	CatchUp                   bool    `json:"catch_up"`
	ResumeIncompleteOnStartup bool    `json:"resume_incomplete_on_start"`
	ResumeCancelledOnStartup  bool    `json:"resume_cancelled_on_start"`
	PolicyVersion             int     `json:"policy_version,omitempty"`
	FailurePolicy             string  `json:"failure_policy"`
}
type ChainStep struct {
	TaskID      int64  `json:"task_id"`
	Name        string `json:"name"`
	Status      string `json:"status"`
	ExecutionID int64  `json:"execution_id,omitempty"`
	Error       string `json:"error,omitempty"`
}
type ChainRun struct {
	ID            int64       `json:"id"`
	ChainID       int64       `json:"chain_id"`
	Day           string      `json:"day"`
	Status        string      `json:"status"`
	FailurePolicy string      `json:"failure_policy"`
	Steps         []ChainStep `json:"steps"`
	UpdatedAt     time.Time   `json:"updated_at"`
}

func (s *Store) migrateChains() error {
	_, err := s.db.Exec(`CREATE TABLE IF NOT EXISTS chains (id INTEGER PRIMARY KEY AUTOINCREMENT, definition TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS chain_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, chain_id INTEGER NOT NULL REFERENCES chains(id), day TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(chain_id,day));
 CREATE TABLE IF NOT EXISTS chain_execution_links (execution_id INTEGER PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE, run_id INTEGER NOT NULL REFERENCES chain_runs(id));`)
	return err
}
func normalizeChainPolicy(c *Chain) {
	// Chains saved before startup-resume policy existed should gain the safe
	// default: recover failed/interrupted work from a previous daemon session,
	// but never restart an operator-cancelled chain unless explicitly enabled.
	if c.PolicyVersion == 0 {
		c.ResumeIncompleteOnStartup = true
		c.ResumeCancelledOnStartup = false
		c.PolicyVersion = 1
	}
}

func (s *Store) ListChains() ([]Chain, error) {
	rows, err := s.db.Query(`SELECT id,definition FROM chains ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Chain{}
	for rows.Next() {
		var id int64
		var b string
		if err = rows.Scan(&id, &b); err != nil {
			return nil, err
		}
		var c Chain
		if err = json.Unmarshal([]byte(b), &c); err != nil {
			return nil, err
		}
		normalizeChainPolicy(&c)
		c.ID = id
		out = append(out, c)
	}
	return out, rows.Err()
}
func (s *Store) GetChain(id int64) (Chain, error) {
	var b string
	err := s.db.QueryRow(`SELECT definition FROM chains WHERE id=?`, id).Scan(&b)
	if errors.Is(err, sql.ErrNoRows) {
		err = ErrNotFound
	}
	var c Chain
	if err == nil {
		err = json.Unmarshal([]byte(b), &c)
		if err == nil {
			normalizeChainPolicy(&c)
		}
	}
	c.ID = id
	return c, err
}
func (s *Store) SaveChain(c Chain, disablePlans bool) (Chain, error) {
	normalizeChainPolicy(&c)
	tx, err := s.db.Begin()
	if err != nil {
		return c, err
	}
	defer tx.Rollback()
	b, err := json.Marshal(c)
	if err != nil {
		return c, err
	}
	if c.ID == 0 {
		r, e := tx.Exec(`INSERT INTO chains(definition) VALUES(?)`, string(b))
		if e != nil {
			return c, e
		}
		c.ID, err = r.LastInsertId()
	} else {
		r, e := tx.Exec(`UPDATE chains SET definition=? WHERE id=?`, string(b), c.ID)
		if e != nil {
			return c, e
		}
		if n, _ := r.RowsAffected(); n == 0 {
			return c, ErrNotFound
		}
	}
	if err != nil {
		return c, err
	}
	if disablePlans {
		for _, id := range c.TaskIDs {
			if _, err = tx.Exec(`UPDATE plans SET enabled=0 WHERE task_id=?`, id); err != nil {
				return c, err
			}
		}
	}
	return c, tx.Commit()
}
func (s *Store) CreateChainRun(c Chain, day string) (ChainRun, error) {
	if previous, err := s.ChainRunForDay(c.ID, day); err == nil {
		return previous, nil
	} else if !errors.Is(err, ErrNotFound) {
		return ChainRun{}, err
	}
	r := ChainRun{ChainID: c.ID, Day: day, Status: "running", FailurePolicy: c.FailurePolicy, UpdatedAt: time.Now().UTC()}
	for _, id := range c.TaskIDs {
		t, err := s.GetTask(id)
		if err != nil {
			t.Name = fmt.Sprintf("Missing task #%d", id)
		}
		r.Steps = append(r.Steps, ChainStep{TaskID: id, Name: t.Name, Status: "pending"})
	}
	b, err := json.Marshal(r)
	if err != nil {
		return r, err
	}
	v, err := s.db.Exec(`INSERT INTO chain_runs(chain_id,day,state) VALUES(?,?,?) ON CONFLICT(chain_id,day) DO NOTHING`, c.ID, day, string(b))
	if err != nil {
		return r, err
	}
	if n, _ := v.RowsAffected(); n == 0 {
		return s.ChainRunForDay(c.ID, day)
	}
	r.ID, err = v.LastInsertId()
	return r, err
}
func scanChainRun(row interface{ Scan(...any) error }) (ChainRun, error) {
	var r ChainRun
	var b string
	err := row.Scan(&r.ID, &b)
	if errors.Is(err, sql.ErrNoRows) {
		err = ErrNotFound
	}
	id := r.ID
	if err == nil {
		err = json.Unmarshal([]byte(b), &r)
	}
	r.ID = id
	return r, err
}
func (s *Store) ChainRunForDay(id int64, day string) (ChainRun, error) {
	return scanChainRun(s.db.QueryRow(`SELECT id,state FROM chain_runs WHERE chain_id=? AND day=?`, id, day))
}
func (s *Store) GetChainRun(id int64) (ChainRun, error) {
	return scanChainRun(s.db.QueryRow(`SELECT id,state FROM chain_runs WHERE id=?`, id))
}
func (s *Store) ListChainRuns() ([]ChainRun, error) {
	rows, err := s.db.Query(`SELECT id,state FROM chain_runs ORDER BY id DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ChainRun{}
	for rows.Next() {
		r, e := scanChainRun(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
func (s *Store) SaveChainRun(r ChainRun) error {
	r.UpdatedAt = time.Now().UTC()
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	_, err = s.db.Exec(`UPDATE chain_runs SET state=? WHERE id=?`, string(b), r.ID)
	return err
}

// Persist the pending execution AND its step link before the worker can launch.
// A crash in any subsequent instruction cannot cause an unrecorded second run.
func (s *Store) CreateChainExecution(runID int64, index int, e Execution) (Execution, error) {
	tx, err := s.db.Begin()
	if err != nil {
		return e, err
	}
	defer tx.Rollback()
	r, err := scanChainRun(tx.QueryRow(`SELECT id,state FROM chain_runs WHERE id=?`, runID))
	if err != nil {
		return e, err
	}
	if r.Status != "running" || index < 0 || index >= len(r.Steps) || r.Steps[index].Status != "pending" || r.Steps[index].TaskID != e.TaskID {
		return e, fmt.Errorf("chain step is not pending")
	}
	e.CreatedAt = time.Now().UTC()
	res, err := tx.Exec(`INSERT INTO executions(task_id,trigger,status,created_at) VALUES(?,?,?,?)`, e.TaskID, e.Trigger, e.Status, e.CreatedAt)
	if err != nil {
		return e, err
	}
	e.ID, err = res.LastInsertId()
	if err != nil {
		return e, err
	}
	r.Steps[index].ExecutionID = e.ID
	r.Steps[index].Status = "running"
	r.UpdatedAt = e.CreatedAt
	b, err := json.Marshal(r)
	if err != nil {
		return e, err
	}
	if _, err = tx.Exec(`UPDATE chain_runs SET state=? WHERE id=?`, string(b), r.ID); err != nil {
		return e, err
	}
	if _, err = tx.Exec(`INSERT INTO chain_execution_links(execution_id,run_id) VALUES(?,?)`, e.ID, r.ID); err != nil {
		return e, err
	}
	return e, tx.Commit()
}

// DeleteChainRun removes one finished daily-chain occurrence while retaining
// its underlying task execution rows as independent execution history.
// Running/paused occurrences must be stopped or cancelled first.
func (s *Store) DeleteChainRun(id int64) error {
	r, err := s.GetChainRun(id)
	if err != nil {
		return err
	}
	if r.Status == "running" || r.Status == "paused" {
		return fmt.Errorf("chain run %d is still %s", id, r.Status)
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.Exec(`DELETE FROM chain_execution_links WHERE run_id=?`, id); err != nil {
		return err
	}
	res, err := tx.Exec(`DELETE FROM chain_runs WHERE id=?`, id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return tx.Commit()
}

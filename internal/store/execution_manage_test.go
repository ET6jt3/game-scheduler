package store

import (
	"errors"
	"testing"
)

func TestDeleteExecutionTerminalOnly(t *testing.T) {
	s := newTestStore(t)
	mkGame(t, s, "g")
	task, err := s.CreateTask(Task{GameID: "g", Name: "t", Type: "raw", Enabled: true})
	if err != nil {\n\t\tt.Fatal(err)\n\t}

	for _, status := range []string{StatusFailed, StatusCancelled, StatusSuccess} {
		e, err := s.CreateExecution(Execution{TaskID: task.ID, Trigger: TriggerManual, Status: status})
		if err != nil { t.Fatal(err) }
		deleted, err := s.DeleteExecution(e.ID)
		if err != nil {\n\t\t\tt.Fatalf("delete %s: %v", status, err)\n\t\t}
		if deleted.Status != status {\n\t\t\tt.Fatalf("deleted status=%q want %q", deleted.Status, status)\n\t\t}
		if _, err := s.GetExecution(e.ID); !errors.Is(err, ErrNotFound) {\n\t\t\tt.Fatalf("row still exists: %v", err)\n\t\t}
	}
}

func TestDeleteExecutionRejectsActive(t *testing.T) {
	s := newTestStore(t)
	mkGame(t, s, "g")
	task, err := s.CreateTask(Task{GameID: "g", Name: "t", Type: "raw", Enabled: true})
	if err != nil { t.Fatal(err) }

	for _, status := range []string{StatusPending, StatusRunning} {
		e, err := s.CreateExecution(Execution{TaskID: task.ID, Trigger: TriggerManual, Status: status})
		if err != nil { t.Fatal(err) }
		if _, err := s.DeleteExecution(e.ID); !errors.Is(err, ErrExecutionActive) {
			t.Fatalf("delete %s err=%v, want ErrExecutionActive", status, err)
		}
		if _, err := s.GetExecution(e.ID); err != nil {\n\t\t\tt.Fatalf("active row was removed: %v", err)\n\t\t}
	}
}

package singleinstance

import (
	"path/filepath"
	"testing"
)

func TestExclusiveAndRelease(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lock")
	release, e := Acquire(path)
	if e != nil {
		t.Fatal(e)
	}
	if other, e := Acquire(path); e == nil {
		other()
		t.Fatal("second instance admitted")
	}
	release()
	release, e = Acquire(path)
	if e != nil {
		t.Fatal(e)
	}
	release()
}

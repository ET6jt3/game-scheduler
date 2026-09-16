// Package singleinstance prevents a second server from reconciling a live server's database.
package singleinstance

import (
	"fmt"
	"os"
)

func Acquire(path string) (func(), error) {
	f, e := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = lock(f); e != nil {
		f.Close()
		return nil, fmt.Errorf("another server owns this database: %w", e)
	}
	return func() { _ = f.Close() }, nil
}

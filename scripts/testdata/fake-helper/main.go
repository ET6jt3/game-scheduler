// A harmless subprocess fixture. Never launches a game or opens helper files.
package main

import (
	"encoding/json"
	"os"
)

func main() {
	cwd, e := os.Getwd()
	if e != nil {
		panic(e)
	}
	if e = json.NewEncoder(os.Stdout).Encode(map[string]any{"args": os.Args[1:], "working_dir": cwd}); e != nil {
		panic(e)
	}
}

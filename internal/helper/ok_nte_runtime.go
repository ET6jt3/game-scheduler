package helper

import _ "embed"

// Embedded in server.exe; executed in the helper's packaged Python interpreter.
// No script is installed into the external helper or game directories.
//
//go:embed ok_nte_bootstrap.py
var okNTEHeadlessBootstrap string

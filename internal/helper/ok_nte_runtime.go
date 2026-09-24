package helper

import (
	"bytes"
	"compress/zlib"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// The extension is compressed only for Windows command-line transport. Both
// sources are embedded in the server, readable in the repository, and executed
// in memory. The external helper/game files are never patched.
//
//go:embed ok_nte_bootstrap.py
var okNTECoreSource string

//go:embed _gs_nte_launcher.py
var okNTELauncherSource string

var okNTEHeadlessBootstrap = buildNTEBootstrap()

func buildNTEBootstrap() string {
	var data bytes.Buffer
	writer, err := zlib.NewWriterLevel(&data, zlib.BestCompression)
	if err != nil {
		panic(err)
	}
	if _, err = writer.Write([]byte(okNTELauncherSource)); err != nil {
		panic(err)
	}
	if err = writer.Close(); err != nil {
		panic(err)
	}
	exe, err := os.Executable()
	if err != nil {
		panic(err)
	}
	root := filepath.Dir(exe)
	if strings.EqualFold(filepath.Base(root), "App") {
		root = filepath.Dir(root)
	}
	logDir, _ := json.Marshal(filepath.Join(root, "Logs", "ok-nte"))
	prefix := "from __future__ import annotations\nimport sys,types,zlib,base64,os\n" +
		"_gs=types.ModuleType('_gs_nte_launcher');sys.modules[_gs.__name__]=_gs\n" +
		"exec(compile(zlib.decompress(base64.b64decode('" + base64.StdEncoding.EncodeToString(data.Bytes()) +
		"')), '<gs-nte-launcher>', 'exec'),_gs.__dict__)\n" +
		"os.environ.setdefault('GS_OK_NTE_EVENT_DIR', " + string(logDir) + ")\n"
	return prefix + strings.Replace(okNTECoreSource, "from __future__ import annotations\n", "", 1)
}

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

// Both readable Python sources are embedded in server.exe and compressed only
// for Windows command-line transport. The external helper/game files are never
// patched or rewritten.
//
//go:embed ok_nte_bootstrap.py
var okNTECoreSource string

//go:embed _gs_nte_launcher.py
var okNTELauncherSource string

//go:embed ok_nte_native_lifecycle.py
var okNTENativeLifecycleSource string

var okNTEHeadlessBootstrap = buildNTEBootstrap()
var okNTENativeLifecycleBootstrap = buildNTENativeBootstrap()

func compressNTETransport(source string) string {
	var data bytes.Buffer
	writer, err := zlib.NewWriterLevel(&data, zlib.BestCompression)
	if err != nil {
		panic(err)
	}
	if _, err = writer.Write([]byte(source)); err != nil {
		panic(err)
	}
	if err = writer.Close(); err != nil {
		panic(err)
	}
	return base64.StdEncoding.EncodeToString(data.Bytes())
}

func buildNTEBootstrap() string {
	launcher := compressNTETransport(okNTELauncherSource)
	core := compressNTETransport(okNTECoreSource)
	exe, err := os.Executable()
	if err != nil {
		panic(err)
	}
	root := filepath.Dir(exe)
	if strings.EqualFold(filepath.Base(root), "App") {
		root = filepath.Dir(root)
	}
	logDir, _ := json.Marshal(filepath.Join(root, "Logs", "ok-nte"))
	return "import sys,types,zlib,base64,os\n" +
		"_gs=types.ModuleType('_gs_nte_launcher');sys.modules[_gs.__name__]=_gs\n" +
		"exec(compile(zlib.decompress(base64.b64decode('" + launcher +
		"')), '<gs-nte-launcher>', 'exec'),_gs.__dict__)\n" +
		"os.environ.setdefault('GS_OK_NTE_EVENT_DIR', " + string(logDir) + ")\n" +
		"exec(compile(zlib.decompress(base64.b64decode('" + core +
		"')), '<gs-nte-core>', 'exec'),globals())\n"
}

func buildNTENativeBootstrap() string {
	source := compressNTETransport(okNTENativeLifecycleSource)
	exe, err := os.Executable()
	if err != nil {
		panic(err)
	}
	root := filepath.Dir(exe)
	if strings.EqualFold(filepath.Base(root), "App") {
		root = filepath.Dir(root)
	}
	logDir, _ := json.Marshal(filepath.Join(root, "Logs", "ok-nte"))
	return "import zlib,base64,os\n" +
		"os.environ.setdefault('GS_OK_NTE_EVENT_DIR', " + string(logDir) + ")\n" +
		"exec(compile(zlib.decompress(base64.b64decode('" + source +
		"')), '<gs-nte-native>', 'exec'),globals())\n"
}

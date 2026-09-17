//go:build windows

package chains

import (
	"golang.org/x/sys/windows"
	"strings"
	"unsafe"
)

var user32 = windows.NewLazySystemDLL("user32.dll")
var openDesktop = user32.NewProc("OpenInputDesktop")
var closeDesktop = user32.NewProc("CloseDesktop")
var desktopInfo = user32.NewProc("GetUserObjectInformationW")

// A locked/disconnected session cannot provide the interactive Default desktop.
func DesktopReady() bool {
	h, _, _ := openDesktop.Call(0, 0, 1)
	if h == 0 {
		return false
	}
	defer closeDesktop.Call(h)
	var name [256]uint16
	var size uint32
	ok, _, _ := desktopInfo.Call(h, 2, uintptr(unsafe.Pointer(&name[0])), uintptr(len(name)*2), uintptr(unsafe.Pointer(&size)))
	return ok != 0 && strings.EqualFold(windows.UTF16ToString(name[:]), "Default")
}

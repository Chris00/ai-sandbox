"""The seccomp filter that ai gives bwrap, applied to a process here.

The fake sandbox records the filter; a child process then sets it on
itself and makes the system calls, as a process in the sandbox would.
Each is also made without the filter, so that a result can't be the
kernel's own refusal.
"""
import errno
import json
import subprocess
import sys
import unittest

from harness import AiTestCase

PROBE = r'''
import ctypes, json, os, socket, sys
import seccomp

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
NR = {name: seccomp.resolve_syscall(seccomp.Arch.NATIVE, name)
      for name in ("kcmp", "name_to_handle_at", "socketpair", "socket")}

if sys.argv[1] != "-":
    with open(sys.argv[1], "rb") as f:
        data = f.read()

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    prog = Prog(len(data) // 8, ctypes.cast(buf, ctypes.c_void_p))
    PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 38, 22, 2
    assert libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0
    assert libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) == 0


def call(name, *args):
    """0 if the call succeeds, its errno otherwise."""
    ctypes.set_errno(0)
    r = libc.syscall(ctypes.c_long(NR[name]), *(ctypes.c_long(a) for a in args))
    if r < 0:
        return ctypes.get_errno()
    if name in ("socket",):
        os.close(r)
    return 0


def pair(family, kind=socket.SOCK_STREAM):
    fds = (ctypes.c_int * 2)()
    ctypes.set_errno(0)
    r = libc.syscall(ctypes.c_long(NR["socketpair"]), ctypes.c_long(family),
                     ctypes.c_long(kind), ctypes.c_long(0), fds)
    if r < 0:
        return ctypes.get_errno()
    os.close(fds[0]), os.close(fds[1])
    return 0


handle = ctypes.create_string_buffer(8)        # handle_bytes 0: too small
mount_id = ctypes.c_int()
results = {
    "kcmp": call("kcmp", os.getpid(), os.getpid(), 0, 0, 0),    # KCMP_FILE, fd 0 and 0
    "name_to_handle_at": call("name_to_handle_at", -100,       # AT_FDCWD
                              ctypes.addressof(ctypes.create_string_buffer(b"/")),
                              ctypes.addressof(handle), ctypes.addressof(mount_id), 0),
    "socketpair unix": pair(socket.AF_UNIX),
    "socketpair packet": pair(socket.AF_PACKET, socket.SOCK_DGRAM),
    "socketpair unix, high bits": pair((1 << 32) | socket.AF_UNIX),
    "socket packet": call("socket", socket.AF_PACKET, socket.SOCK_DGRAM, 0),
    "socket unix, high bits": call("socket", (1 << 32) | socket.AF_UNIX, socket.SOCK_STREAM, 0),
    "socket inet": call("socket", socket.AF_INET, socket.SOCK_STREAM, 0),
    "socket netlink route": call("socket", socket.AF_NETLINK, socket.SOCK_RAW, 0),
}
print(json.dumps(results))
'''


try:
    import seccomp  # noqa: F401
    HAVE_SECCOMP = True
except ImportError:
    HAVE_SECCOMP = False


@unittest.skipUnless(HAVE_SECCOMP, "needs libseccomp's Python bindings, as ai does")
class Filter(AiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}

    def probe(self, with_filter):
        if with_filter not in self.results:
            if with_filter:
                r = self.ai("-w")
                self.assertOk(r)
                path = str(r.log / "seccomp.bpf")
            else:
                path = "-"
            p = subprocess.run([sys.executable, "-I", "-c", PROBE, path],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            type(self).results[with_filter] = json.loads(p.stdout)
        return self.results[with_filter]

    def assertBlocked(self, name, err, control=True):
        """`name` fails with `err` under the filter.  control: and not without
        it, on any machine; otherwise, the kernel or the machine may refuse it
        already (AF_PACKET without its module, say)."""
        if control:
            self.assertNotEqual(self.probe(False)[name], err, f"{name}: refused anyway")
        self.assertEqual(self.probe(True)[name], err, name)

    def assertAllowed(self, name):
        self.assertEqual(self.probe(False)[name], 0, name)
        self.assertEqual(self.probe(True)[name], 0, name)

    def test_kcmp(self):
        self.assertBlocked("kcmp", errno.ENOSYS)

    def test_name_to_handle_at(self):
        self.assertBlocked("name_to_handle_at", errno.ENOSYS)

    def test_socketpair_families(self):
        self.assertAllowed("socketpair unix")
        self.assertBlocked("socketpair packet", errno.EAFNOSUPPORT, control=False)
        self.assertBlocked("socketpair unix, high bits", errno.EAFNOSUPPORT)

    def test_socket_families(self):
        self.assertAllowed("socket inet")
        self.assertAllowed("socket netlink route")
        self.assertBlocked("socket packet", errno.EAFNOSUPPORT, control=False)
        # Some sandboxes refuse it already, as ai's does: not a control.
        self.assertBlocked("socket unix, high bits", errno.EAFNOSUPPORT, control=False)


if __name__ == "__main__":
    unittest.main()

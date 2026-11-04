#!/usr/bin/python3 -I
"""Stand for your shell: lead the terminal's session and outlive `ai`.

When a session's leader exits, the kernel hangs up the processes of its
terminal, which would clean up after `ai`, as it never can count on:
what it leaves behind would go unnoticed.

Usage: session_leader.py STATUS_FD RELEASE_FD PROGRAM ARG...

Writes the exit status of PROGRAM to STATUS_FD, then waits for the end
of file on RELEASE_FD.
"""
import fcntl, os, subprocess, sys, termios

status_fd, release_fd = int(sys.argv[1]), int(sys.argv[2])
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)    # the terminal, given as stdin
null = os.open(os.devnull, os.O_RDONLY)
proc = subprocess.Popen([os.fsencode(a) for a in sys.argv[3:]], stdin=null)
# As the shell would report it: negative for a signal.
os.write(status_fd, b"%d\n" % proc.wait())
os.close(status_fd)
os.read(release_fd, 1)

# PROOT-BENCH-01 end of one run. Runs OUTSIDE PRoot as the Termux uid in the run-as SELinux domain (Termux python),
# like proot_meter.py: the daily PRoot cannot see the processes launched through `adb run-as` (dry-run 01 reported
# survivors_after_kill=0 because its /proc scan saw nothing, and ended nothing).
# CONSTRUCTION: TERM every process whose session id is the guest's, KILL what is left after 10 s, then wait up to
# 10 s for the tracer (proot pid, checked against its expected arg0 before any signal) and TERM it if still alive.
# Every signal is logged with pid and cmdline. Last line: END survivors=<n> tracer_gone=<true|false>.
#   python proot_bench_end.py <sid-file> <tracer-pid-file> <tracer-arg0> <log>
import os, signal, sys, time
sidf, tpf, targ0, log = sys.argv[1:5]
sid, tp = int(open(sidf).read()), int(open(tpf).read())
L = open(log, 'a')


def sess(pid):
    try:
        s = open(f'/proc/{pid}/stat').read()
        return int(s[s.rfind(')') + 2:].split()[3])
    except Exception:
        return None


def cmd(pid):
    try:
        return open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\0', b' ').decode(errors='replace')[:160]
    except Exception:
        return '?'


def members():
    return [int(p) for p in os.listdir('/proc') if p.isdigit() and int(p) != os.getpid() and sess(p) == sid]


def send(pids, sig, what):
    for p in pids:
        c = cmd(p)
        try:
            os.kill(p, sig); L.write(f'{what} sid={sid} pid={p} cmd={c}\n')
        except ProcessLookupError:
            pass
        except Exception as e:
            L.write(f'{what}_failed sid={sid} pid={p} err={e} cmd={c}\n')


L.write(f'end_begin sid={sid} tracer={tp} members={len(members())}\n')
send(members(), signal.SIGTERM, 'term')
for _ in range(20):
    if not members():
        break
    time.sleep(0.5)
send(members(), signal.SIGKILL, 'kill')
time.sleep(1)
left = len(members())
gone = False
for _ in range(20):
    if not os.path.exists(f'/proc/{tp}'):
        gone = True; break
    time.sleep(0.5)
if not gone:
    a0 = cmd(tp).split(' ')[0]
    if a0 == targ0:
        send([tp], signal.SIGTERM, 'term_tracer'); time.sleep(2)
        gone = not os.path.exists(f'/proc/{tp}')
    else:
        L.write(f'tracer_pid_reused pid={tp} arg0={a0!r} want={targ0!r}; not signalled\n')
L.write(f'END survivors={left} tracer_gone={str(gone).lower()}\n'); L.close()
print(f'END survivors={left} tracer_gone={str(gone).lower()}')

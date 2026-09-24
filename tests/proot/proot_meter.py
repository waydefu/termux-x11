# PROOT-BENCH-01 CPU meter. Runs OUTSIDE PRoot as the Termux uid in the run-as SELinux domain (Termux python), so
# it is not traced by anything and can read the /proc of the Cursor launched through `adb run-as` (the daily
# PRoot, untrusted_app, cannot: probe dry-run 01 read session_procs=0 / tracer ticks null from there).
# Every INTERVAL s it appends: epoch_ms, session_ticks (utime+stime+cutime+cstime of every live process whose
# session id is the guest's), session_procs, tracer_ticks (utime+stime of the proot pid), x3_ticks.
# The full /proc walk (session membership) runs every RESCAN-th sample; in between only known members are read.
# A process found late still contributes all its ticks since birth, so membership lag only shifts ticks by < 1 s.
#   python proot_meter.py <out.csv> <stop-file> <sid-file> <tracer-pid-file> <x3pid> [interval=0.2]
import os, sys, time
out, stop, sidf, tpf, x3 = sys.argv[1:6]
iv = float(sys.argv[6]) if len(sys.argv) > 6 else 0.2
RESCAN = 5


def fields(pid):
    s = open(f'/proc/{pid}/stat').read()
    return s[s.rfind(')') + 2:].split()      # f[0] = state (field 3)


def read_int(path):
    try:
        return int(open(path).read().strip())
    except Exception:
        return None


t0 = time.time()
while (read_int(sidf) is None or read_int(tpf) is None) and time.time() - t0 < 60 and not os.path.exists(stop):
    time.sleep(0.1)
sid, tp = read_int(sidf), read_int(tpf)
members = set()
with open(out, 'w') as f:
    try:
        arg0 = open(f'/proc/{tp}/cmdline', 'rb').read().split(b'\0')[0].decode()
    except Exception:
        arg0 = 'null'
    f.write(f'# sid={sid} tracer_pid={tp} tracer_arg0={arg0} x3={x3} interval={iv} rescan={RESCAN}\n')
    f.write('epoch_ms,session_ticks,session_procs,tracer_ticks,x3_ticks\n')
    i = 0
    while not os.path.exists(stop):
        tick = time.time()
        if i % RESCAN == 0:
            members |= {p for p in os.listdir('/proc') if p.isdigit()}
        i += 1
        ssum = 0
        for p in list(members):
            try:
                fl = fields(p)
                if int(fl[3]) != sid:
                    members.discard(p); continue
                ssum += int(fl[11]) + int(fl[12]) + int(fl[13]) + int(fl[14])
            except Exception:
                members.discard(p)
        try:
            fl = fields(tp); tt = int(fl[11]) + int(fl[12])
        except Exception:
            tt = ''
        try:
            fl = fields(x3); xt = int(fl[11]) + int(fl[12])
        except Exception:
            xt = ''
        f.write(f'{int(tick * 1000)},{ssum},{len(members)},{tt},{xt}\n'); f.flush()
        time.sleep(max(0.0, iv - (time.time() - tick)))

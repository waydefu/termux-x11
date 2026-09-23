# bb-diff.awk - one blackbox line from two /proc/[0-9]*/stat snapshots.
#   awk -v dt=5.0 -v hz=100 -v top=8 -f bb-diff.awk PREV CUR
# Prints: proot_cores=<n> majflt_per_s=<n> nprocs=<n> top=<comm>:<pid>:<cores>,...
# A pid present in CUR but not in PREV counts from 0 (it was born inside the window).
function parse(line,   p, q, rest, n, f) {
    p = index(line, " (")
    if (p == 0) return 0
    PID = substr(line, 1, p - 1)
    q = match(line, /\) [A-Za-z] /)          # end of comm: ") <state> "
    if (q == 0) return 0
    COMM = substr(line, p + 2, q - p - 2)
    gsub(/[ ,:=]/, "_", COMM)
    rest = substr(line, q + 2)
    n = split(rest, f, " ")
    if (n < 13) return 0
    TICKS = f[12] + f[13]                      # utime + stime
    MAJ = f[10]                                # majflt
    return 1
}
FNR == NR { if (parse($0)) { pt[PID] = TICKS; pm[PID] = MAJ }; next }
{
    if (!parse($0)) next
    nprocs++
    d = TICKS - ((PID in pt) ? pt[PID] : 0)
    m = MAJ - ((PID in pm) ? pm[PID] : 0)
    if (d < 0) d = 0
    if (m > 0) maj += m
    if (COMM == "proot") proot += d
    if (d > 0) { cnt++; dk[cnt] = d; nm[cnt] = COMM ":" PID }
}
END {
    out = ""
    for (k = 1; k <= top && k <= cnt; k++) {       # partial selection sort, cnt is small
        b = k
        for (j = k + 1; j <= cnt; j++) if (dk[j] > dk[b]) b = j
        t = dk[k]; dk[k] = dk[b]; dk[b] = t
        t = nm[k]; nm[k] = nm[b]; nm[b] = t
        out = out (k > 1 ? "," : "") nm[k] ":" sprintf("%.2f", dk[k] / hz / dt)
    }
    printf "proot_cores=%.3f majflt_per_s=%.0f nprocs=%d top=%s", proot / hz / dt, maj / dt, nprocs, out
}

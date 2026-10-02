class_name Fmt
extends RefCounted
## Small formatting helpers shared by UI and logs.


static func money(v: float) -> String:
    var neg: bool = v < 0.0
    var s: String = str(int(round(absf(v))))
    var out: String = ""
    var count: int = 0
    for i in range(s.length() - 1, -1, -1):
        out = s[i] + out
        count += 1
        if count % 3 == 0 and i > 0:
            out = "," + out
    return ("-$" if neg else "$") + out


static func pct(v: float) -> String:
    return "%d%%" % int(round(v * 100.0))

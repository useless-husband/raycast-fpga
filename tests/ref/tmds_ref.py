"""Independent TMDS reference, transcribed from the DVI 1.0 spec flowchart
(section 3.3.3, figure 3-5).  It deliberately uses plain Python lists of bits
rather than any of the RTL's tricks, so the two implementations share nothing
but the spec."""

CTRL_TOKENS = {0b00: 0b1101010100, 0b01: 0b0010101011, 0b10: 0b0101010100, 0b11: 0b1010101011}


def bits(v, n):
    return [(v >> i) & 1 for i in range(n)]


def encode(d, cnt, de=True, ctrl=0):
    """Return (10-bit symbol, new cnt).  cnt is the running disparity (int)."""
    if not de:
        return CTRL_TOKENS[ctrl], 0
    D = bits(d, 8)
    n1d = sum(D)
    q = [0] * 9
    q[0] = D[0]
    if n1d > 4 or (n1d == 4 and D[0] == 0):
        for i in range(1, 8):
            q[i] = 1 - (q[i - 1] ^ D[i])      # XNOR
        q[8] = 0
    else:
        for i in range(1, 8):
            q[i] = q[i - 1] ^ D[i]            # XOR
        q[8] = 1
    n1q = sum(q[:8])
    n0q = 8 - n1q
    out = [0] * 10
    if cnt == 0 or n1q == n0q:
        out[9] = 1 - q[8]
        out[8] = q[8]
        out[0:8] = q[0:8] if q[8] else [1 - b for b in q[0:8]]
        if q[8] == 0:
            cnt = cnt + (n0q - n1q)
        else:
            cnt = cnt + (n1q - n0q)
    elif (cnt > 0 and n1q > n0q) or (cnt < 0 and n0q > n1q):
        out[9] = 1
        out[8] = q[8]
        out[0:8] = [1 - b for b in q[0:8]]
        cnt = cnt + 2 * q[8] + (n0q - n1q)
    else:
        out[9] = 0
        out[8] = q[8]
        out[0:8] = q[0:8]
        cnt = cnt - 2 * (1 - q[8]) + (n1q - n0q)
    return sum(b << i for i, b in enumerate(out)), cnt


def decode(sym):
    """DVI decoder: returns ('ctrl', c) or ('data', byte)."""
    for c, t in CTRL_TOKENS.items():
        if sym == t:
            return "ctrl", c
    s = bits(sym, 10)
    q = s[0:8] if not s[9] else [1 - b for b in s[0:8]]
    d = [q[0]] + [(q[i] ^ q[i - 1]) if s[8] else 1 - (q[i] ^ q[i - 1]) for i in range(1, 8)]
    return "data", sum(b << i for i, b in enumerate(d))


def reachable_disparities():
    """Breadth-first search over the encoder state: every cnt value reachable
    from reset, with the shortest byte sequence that reaches it."""
    prefix = {0: []}
    frontier = [0]
    while frontier:
        nxt = []
        for c in frontier:
            for d in range(256):
                _, c2 = encode(d, c)
                if c2 not in prefix:
                    prefix[c2] = prefix[c] + [d]
                    nxt.append(c2)
        frontier = nxt
    return prefix

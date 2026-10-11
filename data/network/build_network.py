"""
Vasastan (Stockholm) district heating network: topology + demand selection
for MJ2505 Project 2, Steps 1 and 2.

Run:  python build_network.py
Out:  step1/twn_data/*.csv, step2/twn_data/*.csv,
      step1/demand_selected.csv, step2/demand_selected.csv,
      network_summary.csv, map_step1.png, map_step2.png

Coordinates are real WGS84 (latitude/longitude) from Google Maps: street
intersections and building addresses. Pipe lengths are computed from the
coordinates (haversine distance), so pipes follow straight street segments
between intersections. DHNx only uses the 'length' column for costs and
losses; lat/lon are only used for plotting.
"""
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
EXCEL = ROOT / "Demand_profiles_heating.xlsx"

# ---------------------------------------------------------------------------
# 1. Nodes
# ---------------------------------------------------------------------------
# step = first step in which the node exists (Step 2 keeps everything from Step 1)

FORKS = [
    # id, name, lat, lon, step, source
    ("F0", "Dalagatan / Vanadisvägen",      59.3455268, 18.0418130, 1, "intersection"),
    ("F1", "Dalagatan / Frejgatan",         59.3438624, 18.0434500, 1, "intersection"),
    ("F2", "Dalagatan / Karlbergsvägen",    59.3428348, 18.0444744, 1, "intersection"),
    ("F3", "Dalagatan / Odengatan",         59.3415744, 18.0457282, 1, "intersection"),
    ("F4", "Dalagatan / Eastmansvägen",     59.3402003, 18.0471227, 1, "intersection"),
    ("F5", "Sabbatsberg campus junction",   59.3388000, 18.0457000, 1, "ASSUMED point on campus road"),
    ("F6", "Västmannagatan / Vanadisvägen", 59.3458160, 18.0435765, 1, "intersection"),
    ("F7", "Västmannagatan / Frejgatan",    59.3442888, 18.0450918, 1, "intersection"),
    ("F8", "Västmannagatan / Karlbergsv.",  59.3429315, 18.0467083, 1, "intersection"),
    ("F9", "Västmannagatan / Odengatan",    59.3419718, 18.0474077, 1, "intersection"),
    ("F10", "Upplandsgatan / Vanadisvägen", 59.3463133, 18.0448727, 2, "intersection"),
    ("F11", "Upplandsgatan / Frejgatan",    59.3446532, 18.0465316, 2, "intersection"),
    ("F12", "Upplandsgatan / Karlbergsv.",  59.3429924, 18.0481684, 2, "intersection"),
    ("F13", "Upplandsgatan / Odengatan",    59.3423593, 18.0488058, 2, "intersection"),
    ("F14", "Norrtullsgatan / Vanadisvägen", 59.3467594, 18.0471675, 2, "intersection"),
]

PRODUCERS = [
    # id, name, technology, lat, lon, step, fork it connects to, siting note
    ("P0", "Odenplan commuter rail station (Citybanan)", "Heat pump (station exhaust air)",
     59.3453140, 18.0417940, 1, "F0",
     "Real station entrance. Waste heat from tunnel/station ventilation air"),
    ("P1", "Sabbatsberg boiler house", "Biomass (wood pellet) boiler",
     59.3386000, 18.0450000, 1, "F5",
     "ASSUMED site on Sabbatsberg hospital campus; truck access for pellets from Dalagatan"),
    ("P2", "Odenplan station plant (co-located with P0)", "Electric boiler",
     59.3452000, 18.0420500, 2, "F0",
     "ASSUMED: shares building and grid connection with heat pump P0"),
    ("P3", "Odengatan 69 boiler room", "Biogas boiler",
     59.3429008, 18.0518090, 2, "F13",
     "ASSUMED site at real address; Stockholm inner-city gas grid carries biogas"),
    ("P4", "Hemköp supermarket, Karlbergsvägen 24", "Heat pump (supermarket refrigeration)",
     59.3431098, 18.0459542, 2, "F8",
     "Real supermarket; refrigeration condenser heat lifted by heat pump"),
]

# Consumers: demand column in the Excel, building interpretation
CONSUMERS = [
    # id, name, building type, lat, lon, step, fork, Excel column
    ("C0", "Vasa Real, Karlbergsvägen 15", "School",
     59.3425549, 18.0443048, 1, "F2", "Load 6"),
    ("C1", "ibis Styles Odenplan, Västmannagatan 61", "Hotel",
     59.3423708, 18.0467669, 1, "F9", "Load 5"),
    ("C2", "Sabbatsbergs sjukhus, Olivecronas väg 1", "Hospital building (24/7)",
     59.3386375, 18.0477811, 1, "F5", "Load 3"),
    ("C3", "Dalagatan 46", "Residential block",
     59.3411932, 18.0464805, 1, "F3", "Load 4"),
    ("C4", "Västmannagatan 79", "Residential block (small)",
     59.3445962, 18.0445306, 1, "F7", "Load 2"),
    ("C5", "Sabbatsbergsbyn care home, Eastmansvägen 29", "Care home (24/7)",
     59.3387592, 18.0436931, 2, "F5", "Load 11"),
    ("C6", "Matteusskolan, Vanadisvägen 16", "School (large)",
     59.3476480, 18.0478808, 2, "F14", "Load 18"),   # header missing in Excel, column S
    ("C7", "Odengatan 75", "Residential block",
     59.3424364, 18.0497598, 2, "F13", "Load 10"),
    ("C8", "Frejgatan 34", "Residential block",
     59.3450953, 18.0476597, 2, "F11", "Load 16"),
    ("C9", "Upplandsgatan 82", "Residential block",
     59.3462121, 18.0457273, 2, "F10", "Load 22"),
]

# Candidate street pipes between forks (main network). DHNx picks which to build.
STREET_PIPES = [
    # from, to, street, step
    ("F0", "F1", "Dalagatan", 1), ("F1", "F2", "Dalagatan", 1),
    ("F2", "F3", "Dalagatan", 1), ("F3", "F4", "Dalagatan", 1),
    ("F4", "F5", "Sabbatsberg campus road", 1),
    ("F6", "F7", "Västmannagatan", 1), ("F7", "F8", "Västmannagatan", 1),
    ("F8", "F9", "Västmannagatan", 1),
    ("F0", "F6", "Vanadisvägen", 1), ("F1", "F7", "Frejgatan", 1),
    ("F2", "F8", "Karlbergsvägen", 1), ("F3", "F9", "Odengatan", 1),
    # Step 2 extension eastwards
    ("F10", "F11", "Upplandsgatan", 2), ("F11", "F12", "Upplandsgatan", 2),
    ("F12", "F13", "Upplandsgatan", 2),
    ("F6", "F10", "Vanadisvägen", 2), ("F10", "F14", "Vanadisvägen", 2),
    ("F7", "F11", "Frejgatan", 2), ("F8", "F12", "Karlbergsvägen", 2),
    ("F9", "F13", "Odengatan", 2),
]


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# 2. Demand profiles
# ---------------------------------------------------------------------------
def load_demand():
    raw = pd.read_excel(EXCEL, sheet_name="Sheet1")
    cols = list(raw.columns)
    # Column S has no header in the source file; by position it is Load 18
    cols[18] = "Load 18"
    raw.columns = cols
    raw = raw.set_index("Time")
    # Load 6 is 0 for the last 24 h (31 Dec): data gap, fill with the previous day
    l6 = raw["Load 6"].to_numpy().copy()
    if (l6[-24:] == 0).all():
        l6[-24:] = l6[-48:-24]
        raw["Load 6"] = l6
    return raw  # units assumed kW (hourly mean power), see README


# ---------------------------------------------------------------------------
# 3. Build one step
# ---------------------------------------------------------------------------
def build_step(step, demand):
    out = ROOT / f"step{step}" / "twn_data"
    out.mkdir(parents=True, exist_ok=True)

    forks = [f for f in FORKS if f[4] <= step]
    prods = [p for p in PRODUCERS if p[5] <= step]
    cons = [c for c in CONSUMERS if c[5] <= step]

    # DHNx naming: <component type>-<integer id>
    fork_idx = {f[0]: i for i, f in enumerate(forks)}
    prod_idx = {p[0]: i for i, p in enumerate(prods)}
    cons_idx = {c[0]: i for i, c in enumerate(cons)}
    pos = {f[0]: (f[2], f[3]) for f in forks}
    pos.update({p[0]: (p[3], p[4]) for p in prods})
    pos.update({c[0]: (c[3], c[4]) for c in cons})

    def dhnx_name(node):
        if node in fork_idx:
            return f"forks-{fork_idx[node]}"
        if node in prod_idx:
            return f"producers-{prod_idx[node]}"
        return f"consumers-{cons_idx[node]}"

    sel = demand[[c[7] for c in cons]].copy()
    sel.columns = [c[0] for c in cons]

    pd.DataFrame({
        "id": range(len(forks)),
        "lat": [f[2] for f in forks], "lon": [f[3] for f in forks],
        "name": [f[1] for f in forks],
    }).to_csv(out / "forks.csv", index=False)

    pd.DataFrame({
        "id": range(len(prods)),
        "lat": [p[3] for p in prods], "lon": [p[4] for p in prods],
        "name": [p[1] for p in prods], "technology": [p[2] for p in prods],
    }).to_csv(out / "producers.csv", index=False)

    pd.DataFrame({
        "id": range(len(cons)),
        "lat": [c[3] for c in cons], "lon": [c[4] for c in cons],
        "P_heat_max": [round(sel[c[0]].max(), 1) for c in cons],   # kW, annual peak
        "name": [c[1] for c in cons], "building_type": [c[2] for c in cons],
        "profile": [c[7] for c in cons],
    }).to_csv(out / "consumers.csv", index=False)

    edges = [(a, b, s) for a, b, s, st in STREET_PIPES if st <= step]
    edges += [(p[0], p[6], "producer connection") for p in prods]
    edges += [(c[0], c[6], "service pipe") for c in cons]
    rows = []
    for i, (a, b, kind) in enumerate(edges):
        # producers are written as from_node, consumers as to_node (DHNx convention)
        if a.startswith("C"):
            a, b = b, a
        length = haversine_m(*pos[a], *pos[b])
        rows.append({"id": i, "from_node": dhnx_name(a), "to_node": dhnx_name(b),
                     "length": round(length, 1), "street": kind,
                     "from_label": a, "to_label": b})
    pipes = pd.DataFrame(rows)
    pipes.to_csv(out / "pipes.csv", index=False)

    # Hourly profiles of the selected consumers (kW), used by oemof in Step 3
    sel.to_csv(out.parent / "demand_selected.csv")
    return forks, prods, cons, pipes, sel, pos


# ---------------------------------------------------------------------------
# 4. Map
# ---------------------------------------------------------------------------
STREET_COLOR = "#9aa0a6"


LAT0, LON0 = 59.3380, 18.0405   # local origin for the map (metres east/north)
SHORT_P = {"P0": "P0 Heat pump (station air)", "P1": "P1 Biomass boiler",
           "P2": "P2 Electric boiler", "P3": "P3 Biogas boiler",
           "P4": "P4 Heat pump (supermarket)"}
# label offsets in points (dx, dy, horizontal alignment)
OFFS = {"P0": (-12, 8, "right"), "P1": (10, -12, "left"), "P2": (-24, -10, "right"),
        "P3": (0, -20, "center"), "P4": (0, 12, "center"),
        "C0": (-10, -6, "right"), "C1": (-10, 2, "right"), "C2": (10, -4, "left"),
        "C3": (10, -6, "left"), "C4": (0, 16, "center"), "C5": (0, 12, "center"),
        "C6": (10, 0, "left"), "C7": (8, -16, "left"), "C8": (10, 0, "left"),
        "C9": (10, -4, "left")}
STREET_LABEL_SEG = {"Dalagatan": ("F1", "F2", -1), "Västmannagatan": ("F6", "F7", 1),
                    "Karlbergsvägen": ("F2", "F8", 1), "Odengatan": ("F3", "F9", -1),
                    "Vanadisvägen": ("F0", "F6", 1), "Frejgatan": ("F1", "F7", -1),
                    "Upplandsgatan": ("F11", "F12", 1),
                    "Sabbatsberg campus road": ("F4", "F5", 1)}


def to_m(lat, lon):
    x = (lon - LON0) * 111_320 * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * 110_574
    return x, y


def plot_step(step, forks, prods, cons, pipes, sel, pos):
    xy = {k: to_m(*v) for k, v in pos.items()}
    fig, ax = plt.subplots(figsize=(9, 11))
    ax.set_aspect("equal")

    for _, r in pipes.iterrows():
        (xa, ya), (xb, yb) = xy[r["from_label"]], xy[r["to_label"]]
        main = r["street"] not in ("service pipe", "producer connection")
        ax.plot([xa, xb], [ya, yb], color=STREET_COLOR if main else "#b0b4ba",
                lw=3.5 if main else 1.4, ls="-" if main else (0, (3, 2)), zorder=1,
                solid_capstyle="round")
        if main:
            ax.text((xa + xb) / 2, (ya + yb) / 2, f"{r['length']:.0f} m",
                    fontsize=7, color="#5f6368", ha="center", va="center",
                    bbox=dict(fc="white", ec="none", pad=0.6), zorder=2)

    for name, (a, b, side) in STREET_LABEL_SEG.items():
        if a not in xy or b not in xy:
            continue
        (xa, ya), (xb, yb) = xy[a], xy[b]
        ang = math.degrees(math.atan2(yb - ya, xb - xa))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        nx, ny = -(yb - ya), (xb - xa)
        n = math.hypot(nx, ny)
        mx, my = (xa + xb) / 2 + side * 16 * nx / n, (ya + yb) / 2 + side * 16 * ny / n
        # shift along the street so it does not sit on the length label
        mx += 0.22 * (xb - xa)
        my += 0.22 * (yb - ya)
        ax.text(mx, my, name, fontsize=8, style="italic", color="#80868b",
                rotation=ang, rotation_mode="anchor", ha="center", va="center")

    for f in forks:
        x, y = xy[f[0]]
        ax.scatter(x, y, s=22, color="#5f6368", zorder=3)
        ax.annotate(f[0], (x, y), xytext=(4, 4), textcoords="offset points",
                    fontsize=6.5, color="#5f6368")
    for p in prods:
        x, y = xy[p[0]]
        ax.scatter(x, y, s=150, marker="s", color="#d93025", edgecolor="white",
                   zorder=4)
        dx, dy, ha = OFFS[p[0]]
        ax.annotate(SHORT_P[p[0]], (x, y), xytext=(dx, dy), textcoords="offset points",
                    fontsize=8, color="#b3261e", weight="bold", ha=ha, va="center")
    for c in cons:
        x, y = xy[c[0]]
        pk = sel[c[0]].max()
        ax.scatter(x, y, s=30 + pk / 2.2, color="#188038", edgecolor="white", zorder=4)
        dx, dy, ha = OFFS[c[0]]
        ax.annotate(f"{c[0]} {c[2]}\n{pk:.0f} kW peak", (x, y), xytext=(dx, dy),
                    textcoords="offset points", fontsize=7.5, color="#137333",
                    ha=ha, va="center")

    # scale bar and north arrow
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    ax.plot([xmin + 20, xmin + 120], [ymin + 20, ymin + 20], color="black", lw=2)
    ax.text(xmin + 70, ymin + 30, "100 m", ha="center", fontsize=8)
    ax.annotate("N", xy=(xmax - 30, ymax - 20), xytext=(xmax - 30, ymax - 80),
                arrowprops=dict(arrowstyle="-|>", color="black"), ha="center",
                fontsize=10, weight="bold")

    n_c, n_p = len(cons), len(prods)
    ax.set_title(f"Step {step}: candidate district heating network, Vasastan, Stockholm\n"
                 f"{n_c} consumers (green, size = peak), {n_p} producers (red), "
                 f"{len(pipes)} candidate pipes\n"
                 "Solid = street pipe candidates (length shown), dashed = service and "
                 "producer connections", fontsize=9.5)
    ax.set_xlabel("Metres east")
    ax.set_ylabel("Metres north")
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(ROOT / f"map_step{step}.png", dpi=170)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 5. Summary numbers for the report
# ---------------------------------------------------------------------------
def summarise(step, cons, pipes, sel):
    tot = sel.sum(axis=1)
    sum_peaks = sel.max().sum()
    print(f"\n=== Step {step} ===")
    for c in cons:
        s = sel[c[0]]
        print(f"{c[0]:3s} {c[7]:8s} {c[2]:27s} peak {s.max():7.1f} kW  "
              f"annual {s.sum()/1000:7.1f} MWh  full-load h {s.sum()/s.max():5.0f}")
    print(f"Sum of individual peaks : {sum_peaks:7.1f} kW")
    print(f"Coincident peak (max of sum): {tot.max():7.1f} kW at hour {tot.idxmax()}")
    print(f"Coincidence factor      : {tot.max()/sum_peaks:.3f}")
    print(f"Annual demand           : {tot.sum()/1000:.1f} MWh")
    main = pipes[~pipes.street.isin(["service pipe", "producer connection"])]
    print(f"Candidate street pipes  : {len(main)}, total {main.length.sum():.0f} m")
    print(f"All candidate pipes     : {len(pipes)}, total {pipes.length.sum():.0f} m")
    return {"step": step, "consumers": len(cons), "sum_of_peaks_kW": round(sum_peaks, 1),
            "coincident_peak_kW": round(tot.max(), 1),
            "coincidence_factor": round(tot.max() / sum_peaks, 3),
            "annual_demand_MWh": round(tot.sum() / 1000, 1),
            "candidate_pipes": len(pipes),
            "candidate_length_m": round(pipes.length.sum())}


if __name__ == "__main__":
    demand = load_demand()
    summary = []
    for step in (1, 2):
        forks, prods, cons, pipes, sel, pos = build_step(step, demand)
        plot_step(step, forks, prods, cons, pipes, sel, pos)
        summary.append(summarise(step, cons, pipes, sel))
    pd.DataFrame(summary).to_csv(ROOT / "network_summary.csv", index=False)

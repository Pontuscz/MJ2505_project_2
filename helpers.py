"""
helpers.py - the ONLY supporting code file of the project.

Everything else lives in the notebooks (step1 ... step5). This file holds code
that more than one notebook needs, so that every step uses *exactly* the same
functions:

    0. Project-wide assumptions           (used by all notebooks - change them HERE only)
    1. Paths + solver selection           (used by all notebooks)
    2. Economics: epc, LCOH               (copied 1:1 from the OEMOF tutorial helpers.py)
    3. Demand data loader                 (Step 1, 2, 3)
    4. DHNx: Vasastan layout, input files, runs, results, maps (Step 1, 2)
    5. oemof helpers: build/solve the heat system, unit summary (Step 3, 4, 5)

Units convention
    DHNx  (Step 1-2): kW, kWh, m, EUR
    oemof (Step 3-5): MW, MWh, EUR, tCO2   (kW -> MW conversion happens in Step 3)
"""

import logging
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# =============================================================================
# 0. PROJECT-WIDE ASSUMPTIONS  (the project description requires that the same
#    assumptions are used in every step -> they live here, in one place)
#    Every value must be justified (source or engineering reasoning) in the report.
# =============================================================================
COUNTRY = "Sweden"          # ASSUMPTION - drives prices, grid CO2 factor and solar profile (Step 3-5)
LATITUDE = 59.33            # deg N, Stockholm - used for the solar irradiance profile (Step 3)
YEAR = 2019                 # year of the hourly time index (matches the placeholder price data)
T_SUPPLY = 80               # degC supply temperature    ASSUMPTION - typical Swedish DH
T_RETURN = 40               # degC return temperature    ASSUMPTION
DELTA_T = T_SUPPLY - T_RETURN
INTEREST_RATE = 0.05        # -  discount rate for annualising investments (tutorial default)
PIPE_LIFETIME = 40          # years, pre-insulated DH pipes   ASSUMPTION

# Demand: the Excel profiles are used as SHAPES and all scaled by one factor, in
# every step. Reason: the assignment fixes the pipe range (DN-25 ... DN-60, max
# 339 kW per pipe in Didrik's table). Unscaled, the five Step 1 consumers need
# 873 kW, but two producers behind one DN-50 each can deliver at most
# 2 x 208.9 = 418 kW -> infeasible. With 0.4 the Step 1 design load is 349 kW.
DEMAND_SCALE = 0.4          # -  ASSUMPTION (see Step 1 section 1)
SIMULTANEITY = 1.0          # -  DHNx design: every consumer at its own peak at the same time

# =============================================================================
# 1. PATHS + SOLVER
# =============================================================================
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"

# The course uses CBC (installed with conda: coin-or-cbc). If CBC is not found on
# this computer we fall back to HiGHS, which is pip-installable (highspy) and is
# supported natively by oemof.solph >= 0.6 via solver="highs".
SOLVER = "cbc" if shutil.which("cbc") else "highs"
# HiGHS prints a long log even with tee=False -> switch it off (CBC needs no options)
SOLVER_OPTIONS = {} if SOLVER == "cbc" else {"output_flag": False}


def _patch_meta_results_for_highs():
    """oemof's meta_results() expects a CBC/GLPK-style results object and crashes
    with solver="highs" (KeyError: 'problem'). DHNx calls it internally, so we
    make it tolerant. With CBC nothing changes."""
    import oemof.solph as solph

    original = solph.processing.meta_results
    if getattr(original, "_patched", False):
        return

    def meta_results(om, undefined=False):
        try:
            return original(om, undefined)
        except (KeyError, TypeError, IndexError):
            return {"objective": om.objective(), "problem": {},
                    "solver": dict(om.es.results) if isinstance(om.es.results, dict) else {}}

    meta_results._patched = True
    solph.processing.meta_results = meta_results


_patch_meta_results_for_highs()

# dhnx.plotting sets the root logger to INFO on import, so oemof prints "Optimization successful."
# after every solve. Import DHNx first, then show only warnings.
import dhnx  # noqa: E402,F401
logging.getLogger().setLevel(logging.WARNING)


# =============================================================================
# 2. ECONOMICS  (identical to "OEMOF Tutorial/helpers.py")
# =============================================================================
def LCOH(invest_cost, operation_cost, heat_produced, revenue=0, i=0.05, n=20):
    pvf = ((1 + i) ** n - 1) / ((1 + i) ** n * i)

    return (invest_cost + pvf * (operation_cost - revenue)) / (
        pvf * heat_produced
    )


def epc(invest_cost, i=0.05, n=20):
    """Equivalent periodical (annual) cost of an investment."""
    af = (i * (1 + i) ** n) / ((1 + i) ** n - 1)

    return invest_cost * af


# =============================================================================
# 3. DEMAND DATA
# =============================================================================
def load_demand_profiles():
    """Return the 50 hourly heat-demand profiles (8760 rows x 50 columns, kW, UNSCALED).

    The Excel file is slow to read (~30 s), so the first call caches Sheet1 as
    data/demand_profiles.csv and later calls read the CSV instead.
    Data fixes (state them in the report):
      - column 'Unnamed: 18' (missing header) is renamed 'Load 18'
      - Load 6 is 0 for the last 24 h (31 Dec, a data gap) -> filled with 30 Dec
    Use demand_scaled() for the demand the model actually uses.
    """
    cache = DATA / "demand_profiles.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col="hour")
    else:
        df = pd.read_excel(DATA / "Demand profiles_heating.xlsx", sheet_name="Sheet1")
        df = df.rename(columns={"Unnamed: 18": "Load 18"}).drop(columns="Time")
        df.index = pd.RangeIndex(len(df), name="hour")
        df.to_csv(cache)
    if (df["Load 6"].iloc[-24:] == 0).all():
        df.loc[df.index[-24:], "Load 6"] = df["Load 6"].iloc[-48:-24].values
    return df


def demand_scaled(profiles):
    """Hourly demand (kW) of the given Excel columns, multiplied by DEMAND_SCALE.
    Every step (1-5) must use this, so the demand is the same everywhere."""
    return load_demand_profiles()[list(profiles)] * DEMAND_SCALE


# =============================================================================
# 4. DHNx: VASTASTAN NETWORK, INPUT FILES, RUNS, RESULTS, MAPS   (Step 1, 2)
#    Units: kW, m, EUR (overnight investment). Layout: real coordinates
#    (Google Maps) around Odenplan, Vasastan, Stockholm.
# =============================================================================
import math  # noqa: E402
from collections import defaultdict  # noqa: E402

PIPE_TYPES_CSV = DATA / "pipe_types.csv"      # Didrik's pipe table (DHNx format)
PIPE_TYPES_BY_STEP = {
    1: ["DN-30", "DN-50"],
    2: ["DN-25", "DN-30", "DN-40", "DN-50", "DN-60"],
}


def selected_demand(step):
    """Hourly scaled demand (kW) of the consumers in a step, columns = C0, C1, ..."""
    cons = CONSUMERS[CONSUMERS.step <= step]
    df = demand_scaled(cons.profile)
    df.columns = cons.key.tolist()
    return df


# --- 4.1 Network layout -----------------------------------------------------
# Coordinates (WGS84) from Google Maps: street intersections and addresses.
# step = first step in which the node exists (Step 2 keeps all Step 1 nodes).

FORKS = pd.DataFrame([
    # key, name, lat, lon, step, note
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
], columns=["key", "name", "lat", "lon", "step", "note"])

PRODUCERS = pd.DataFrame([
    # key, name, technology, lat, lon, step, connects to, siting note
    ("P0", "Odenplan commuter rail station", "Heat pump (station exhaust air)",
     59.3453140, 18.0417940, 1, "F0", "Real station; heat recovery from ventilation air is assumed"),
    ("P1", "Sabbatsberg boiler house", "Biomass (wood pellet) boiler",
     59.3386000, 18.0450000, 1, "F5", "ASSUMED site; truck access for pellets from Dalagatan"),
    ("P2", "Odenplan station plant", "Electric boiler",
     59.3452000, 18.0420500, 2, "F0", "ASSUMED; shares building and grid connection with P0"),
    ("P3", "Odengatan 69 boiler room", "Biogas boiler",
     59.3429008, 18.0518090, 2, "F13", "ASSUMED room at real address; inner-city gas grid carries biogas"),
    ("P4", "Hemköp, Karlbergsvägen 24", "Heat pump (supermarket refrigeration heat)",
     59.3431098, 18.0459542, 2, "F8", "Real supermarket; heat recovery is assumed"),
], columns=["key", "name", "technology", "lat", "lon", "step", "fork", "note"])

CONSUMERS = pd.DataFrame([
    # key, name, building type, lat, lon, step, fork, Excel column
    ("C0", "Vasa Real, Karlbergsvägen 15", "School", 59.3425549, 18.0443048, 1, "F2", "Load 6"),
    ("C1", "ibis Styles Odenplan, Västmannagatan 61", "Hotel", 59.3423708, 18.0467669, 1, "F9", "Load 5"),
    ("C2", "Sabbatsbergs sjukhus, Olivecronas väg 1", "Hospital building (24/7)", 59.3386375, 18.0477811, 1, "F5", "Load 3"),
    ("C3", "Dalagatan 46", "Residential block", 59.3411932, 18.0464805, 1, "F3", "Load 4"),
    ("C4", "Västmannagatan 79", "Residential block (small)", 59.3445962, 18.0445306, 1, "F7", "Load 2"),
    ("C5", "Sabbatsbergsbyn care home, Eastmansvägen 29", "Care home (24/7)", 59.3387592, 18.0436931, 2, "F5", "Load 11"),
    ("C6", "Matteusskolan, Vanadisvägen 16", "School (large)", 59.3476480, 18.0478808, 2, "F14", "Load 18"),
    ("C7", "Odengatan 75", "Residential block", 59.3424364, 18.0497598, 2, "F13", "Load 10"),
    ("C8", "Frejgatan 34", "Residential block", 59.3450953, 18.0476597, 2, "F11", "Load 16"),
    ("C9", "Upplandsgatan 82", "Residential block", 59.3462121, 18.0457273, 2, "F10", "Load 22"),
], columns=["key", "name", "building_type", "lat", "lon", "step", "fork", "profile"])

# Candidate street pipes between junctions. DHNx decides which to build.
STREET_PIPES = pd.DataFrame([
    ("F0", "F1", "Dalagatan", 1), ("F1", "F2", "Dalagatan", 1),
    ("F2", "F3", "Dalagatan", 1), ("F3", "F4", "Dalagatan", 1),
    ("F4", "F5", "Sabbatsberg campus road", 1),
    ("F6", "F7", "Västmannagatan", 1), ("F7", "F8", "Västmannagatan", 1),
    ("F8", "F9", "Västmannagatan", 1),
    ("F0", "F6", "Vanadisvägen", 1), ("F1", "F7", "Frejgatan", 1),
    ("F2", "F8", "Karlbergsvägen", 1), ("F3", "F9", "Odengatan", 1),
    ("F10", "F11", "Upplandsgatan", 2), ("F11", "F12", "Upplandsgatan", 2),
    ("F12", "F13", "Upplandsgatan", 2),
    ("F6", "F10", "Vanadisvägen", 2), ("F10", "F14", "Vanadisvägen", 2),
    ("F7", "F11", "Frejgatan", 2), ("F8", "F12", "Karlbergsvägen", 2),
    ("F9", "F13", "Odengatan", 2),
], columns=["a", "b", "street", "step"])


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two WGS84 points."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --- 4.2 DHNx input files ---------------------------------------------------
def step_dir(step: int) -> Path:
    return DATA / f"step{step}"


def node_tables(step: int):
    forks = FORKS[FORKS.step <= step].reset_index(drop=True)
    prods = PRODUCERS[PRODUCERS.step <= step].reset_index(drop=True)
    cons = CONSUMERS[CONSUMERS.step <= step].reset_index(drop=True)
    names = {}
    names.update({k: f"forks-{i}" for i, k in enumerate(forks.key)})
    names.update({k: f"producers-{i}" for i, k in enumerate(prods.key)})
    names.update({k: f"consumers-{i}" for i, k in enumerate(cons.key)})
    pos = {}
    for df in (forks, prods, cons):
        pos.update({r.key: (r.lat, r.lon) for r in df.itertuples()})
    return forks, prods, cons, names, pos


def candidate_pipes(step: int) -> pd.DataFrame:
    forks, prods, cons, names, pos = node_tables(step)
    edges = [(r.a, r.b, r.street) for r in STREET_PIPES[STREET_PIPES.step <= step].itertuples()]
    edges += [(r.key, r.fork, "producer connection") for r in prods.itertuples()]
    edges += [(r.fork, r.key, "service pipe") for r in cons.itertuples()]
    rows = []
    for i, (a, b, street) in enumerate(edges):
        rows.append({"id": i, "from_node": names[a], "to_node": names[b],
                     "length": round(haversine_m(*pos[a], *pos[b]), 1),
                     "street": street, "from_key": a, "to_key": b})
    return pd.DataFrame(rows)


def write_step_inputs(step: int) -> dict:
    """Write data/step<n>/twn_data, invest_data and demand_selected.csv."""
    out = step_dir(step)
    twn, inv = out / "twn_data", out / "invest_data"
    for d in (twn, inv):
        if d.exists():
            shutil.rmtree(d)
    twn.mkdir(parents=True)
    forks, prods, cons, names, pos = node_tables(step)
    demand = selected_demand(step)

    # --- twn_data: only the columns DHNx needs ---
    pd.DataFrame({"id": range(len(forks)), "lat": forks.lat, "lon": forks.lon}) \
        .to_csv(twn / "forks.csv", index=False)
    pd.DataFrame({"id": range(len(prods)), "lat": prods.lat, "lon": prods.lon}) \
        .to_csv(twn / "producers.csv", index=False)
    p_max = demand.max().round(2)
    pd.DataFrame({"id": range(len(cons)), "lat": cons.lat, "lon": cons.lon,
                  "P_heat_max": p_max[cons.key].values,
                  "profile": cons.profile.values}) \
        .to_csv(twn / "consumers.csv", index=False)   # 'profile' is read by Step 3
    pipes = candidate_pipes(step)
    pipes[["id", "from_node", "to_node", "length"]].to_csv(twn / "pipes.csv", index=False)

    # --- invest_data (same structure as the DHNx tutorial examples) ---
    for d in ("consumers", "producers", "network"):
        (inv / d).mkdir(parents=True)
    (inv / "consumers/bus.csv").write_text(
        "label_2,active,excess,shortage,shortage costs,excess costs\nheat,1,0,0,999999,9999\n")
    (inv / "consumers/demand.csv").write_text("label_2,active,nominal_capacity\nheat,1,1\n")
    (inv / "producers/bus.csv").write_text(
        "label_2,active,excess,shortage,shortage costs,excess costs\nheat,1,0,0,9999,9999\n")
    (inv / "producers/source.csv").write_text("label_2,active\nheat,1\n")
    pt = pd.read_csv(PIPE_TYPES_CSV)
    pt[pt.label_3.isin(PIPE_TYPES_BY_STEP[step])].to_csv(inv / "network/pipes.csv", index=False)

    # --- readable lookup tables + hourly demand for Step 3 ---
    node_info = pd.concat([
        forks.assign(dhnx_id=[names[k] for k in forks.key], kind="fork"),
        prods.assign(dhnx_id=[names[k] for k in prods.key], kind="producer"),
        cons.assign(dhnx_id=[names[k] for k in cons.key], kind="consumer",
                    P_heat_max_kW=p_max[cons.key].values),
    ], ignore_index=True)
    node_info.to_csv(out / "node_info.csv", index=False)
    pipes.to_csv(out / "candidate_pipes_named.csv", index=False)
    demand.to_csv(out / "demand_selected.csv")
    return {"forks": forks, "producers": prods, "consumers": cons,
            "pipes": pipes, "demand": demand, "names": names, "pos": pos}


# --- 4.3 Running DHNx -------------------------------------------------------
# DHNx 0.0.4 creates, for every candidate edge, one binary "build" variable per
# pipe type and per direction. Nothing stops it from building two types in
# parallel on one street (e.g. DN-50 + DN-30 = 262 kW), and its own result code
# then fails with "Pipe id ... already has an investment > 0!". We add the
# missing rule:  sum over types and directions of build[edge] <= 1.

def _edge_key(tag4: str) -> frozenset:
    parts = str(tag4).split("-")      # 'forks-0-producers-1'
    return frozenset(("-".join(parts[0:2]), "-".join(parts[2:4])))


REQUIRE_CONNECTED = True   # see _add_connectivity


def _add_connectivity(m, groups, keys):
    """
    Require ONE connected network: every producer and consumer must be linked
    to every other through built pipes.

    Why: DHNx treats heat as free, so on its own it builds separate islands
    (each producer feeds its neighbours, no pipe between them). Steps 3-5
    model the five producers meeting the AGGREGATE demand, which is only
    physically valid if all producers and consumers share one network.

    How (single-commodity flow formulation): producers-0 sends one
    'connectivity unit' to every other producer and consumer. Units may only
    travel along edges that are built (status = 1). These units are not heat;
    they only prove the built graph is connected.
    """
    import pyomo.environ as po
    nodes = sorted({n for k in keys for n in k})
    terminals = [n for n in nodes if n.startswith(("producers", "consumers"))]
    root = "producers-0"
    big_m = len(terminals)
    arcs = [(a, b) for k in keys for a in k for b in k if a != b]
    m.conn_arcs = po.Set(initialize=arcs, dimen=2)
    m.conn_flow = po.Var(m.conn_arcs, within=po.NonNegativeReals)
    built = {k: sum(m.InvestmentFlowBlock.invest_status[i, o, 0] for i, o in groups[k])
             for k in keys}

    def cap_rule(b, a, c):
        return m.conn_flow[a, c] <= big_m * built[frozenset((a, c))]
    m.conn_capacity = po.Constraint(m.conn_arcs, rule=cap_rule)

    def bal_rule(b, n):
        out_f = sum(m.conn_flow[a, c] for (a, c) in arcs if a == n)
        in_f = sum(m.conn_flow[a, c] for (a, c) in arcs if c == n)
        if n == root:
            return out_f - in_f == len(terminals) - 1
        if n in terminals:
            return in_f - out_f == 1
        return in_f - out_f == 0
    m.conn_balance = po.Constraint(nodes, rule=bal_rule)


def _solve_one_type_per_edge(self):
    import pyomo.environ as po
    import oemof.solph as solph
    from dhnx.optimization.oemof_heatpipe import HeatPipeline

    self.om = solph.Model(self.es)
    m = self.om
    groups = defaultdict(list)
    for (i, o) in m.InvestmentFlowBlock.NON_CONVEX_INVESTFLOWS:
        if isinstance(i, HeatPipeline):
            groups[_edge_key(i.label.tag4)].append((i, o))
    keys = list(groups)
    m.one_pipe_type_per_edge = po.Constraint(
        range(len(keys)),
        rule=lambda b, n: sum(m.InvestmentFlowBlock.invest_status[i, o, 0]
                              for i, o in groups[keys[n]]) <= 1)
    if REQUIRE_CONNECTED:
        _add_connectivity(m, groups, keys)
    s_kw = self.settings["solve_kw"] or {"tee": False}
    m.solve(solver=self.settings["solver"], solve_kwargs=s_kw,
            cmdline_options=self.settings.get("solver_cmdline_options", {}))
    # oemof's result processing cannot read our helper variables (indexed by
    # node names), so remove them after solving. The pipe results are kept.
    for name in ("conn_balance", "conn_capacity", "conn_flow", "conn_arcs"):
        if hasattr(m, name):
            m.del_component(name)
    self.es.results["main"] = solph.processing.results(m)
    self.es.results["meta"] = solph.processing.meta_results(m)


def run_dhnx(step: int, simultaneity: float = SIMULTANEITY, solver: str = SOLVER,
             connected: bool = True):
    """Load data/step<n>, optimise with DHNx, return the ThermalNetwork.

    connected=True adds the one-network requirement (default, used for the
    report). connected=False shows what DHNx does on its own (islands).
    """
    import dhnx
    from dhnx.optimization import optimization_models as om_mod
    global REQUIRE_CONNECTED
    REQUIRE_CONNECTED = connected

    logging.getLogger("dhnx").setLevel(logging.WARNING)
    om_mod.OemofInvestOptimizationModel.solve = _solve_one_type_per_edge
    net = dhnx.network.ThermalNetwork(str(step_dir(step) / "twn_data"))
    invest_opt = dhnx.input_output.load_invest_options(str(step_dir(step) / "invest_data"))
    net.optimize_investment(invest_options=invest_opt, solver=solver,
                            simultaneity=simultaneity,
                            solver_cmdline_options=SOLVER_OPTIONS if solver == SOLVER else {})
    return net


# --- 4.4 Results ------------------------------------------------------------
def _oemof_flows(net) -> dict:
    """{label string: flow at the design hour} for every oemof flow."""
    out = {}
    for (a, b), v in net.results.optimization["oemof"].items():
        if b is None or "flow" not in v["sequences"]:
            continue
        out[(str(a.label), str(b.label))] = float(v["sequences"]["flow"].iloc[0])
    return out


def pipe_results(net, step: int) -> pd.DataFrame:
    """All candidate pipes with DHNx results + street names + design flow."""
    res = net.results.optimization["components"]["pipes"].copy()
    named = pd.read_csv(step_dir(step) / "candidate_pipes_named.csv")
    res = res.reset_index().rename(columns={"index": "id"})
    res["id"] = res["id"].astype(int)
    res = res.merge(named[["id", "street", "from_key", "to_key"]], on="id")
    flows = _oemof_flows(net)
    flow_out = []
    for r in res.itertuples():
        if r.capacity <= 0.001:
            flow_out.append(0.0)
            continue
        a, b = (r.from_node, r.to_node) if r.direction >= 0 else (r.to_node, r.from_node)
        lab = f"infrastructure_heat_{r.hp_type}_{a}-{b}"
        val = [v for (x, y), v in flows.items() if x == lab]
        flow_out.append(val[0] if val else np.nan)
    res["design_flow_kW"] = flow_out          # heat leaving the downstream end
    res["built"] = res.capacity > 0.001
    res["utilisation"] = np.where(res.built, (res.design_flow_kW + res.losses) / res.capacity, 0)
    res["kind"] = np.select(
        [res.street == "service pipe", res.street == "producer connection"],
        ["service", "producer connection"], "street main")
    return res


def producer_supply(net, step: int) -> pd.DataFrame:
    _, prods, *_ = node_tables(step)
    flows = _oemof_flows(net)
    rows = []
    for i, r in prods.iterrows():
        lab = f"producers_heat_source_producers-{i}"
        q = sum(v for (x, _), v in flows.items() if x == lab)
        rows.append({"producer": r.key, "technology": r.technology, "supply_kW": q})
    df = pd.DataFrame(rows)
    df["share"] = df.supply_kW / df.supply_kW.sum()
    return df


def solver_info(net) -> pd.Series:
    """Problem size and solver status (for 'computational complexity')."""
    meta = net.results.optimization["oemof_meta"]
    prob, sol = meta.get("problem", {}) or {}, meta.get("solver", {}) or {}
    term = sol.get("Termination condition")
    return pd.Series({
        "solver": SOLVER,
        "termination": str(term).split(".")[-1].strip("'>: ") if term is not None else "n/a",
        "MIP gap": prob.get("MIPGap"),
        "variables": prob.get("Number of variables"),
        "binary variables": prob.get("Number of binary variables"),
        "constraints": prob.get("Number of constraints"),
        "solve time [s]": round(sol["Wallclock time"], 2) if "Wallclock time" in sol else None,
    })


def summary(net, step: int, pipes: pd.DataFrame) -> pd.Series:
    b = pipes[pipes.built]
    demand = selected_demand(step)
    return pd.Series({
        "consumers": len(node_tables(step)[2]),
        "producers": len(node_tables(step)[1]),
        "pipe types available": len(PIPE_TYPES_BY_STEP[step]),
        "design demand (sum of peaks) [kW]": demand.max().sum() * SIMULTANEITY,
        "heat losses at design hour [kW]": b.losses.sum(),
        "losses / demand [%]": 100 * b.losses.sum() / (demand.max().sum() * SIMULTANEITY),
        "annual heat loss [MWh/yr]": b.losses.sum() * 8760 / 1000,
        "annual demand [MWh/yr]": demand.sum().sum() / 1000,
        "pipes built": len(b),
        "built length [m]": b.length.sum(),
        "candidate length [m]": pipes.length.sum(),
        "pipe investment [EUR]": b.costs.sum(),
        "objective value [EUR]": net.results.optimization["oemof_meta"]["objective"],
    })


def export_network_losses(pipes: pd.DataFrame, path) -> pd.DataFrame:
    """Write the file Step 4 reads: one row, total_loss_kW and annual_loss_MWh.
    l_factor = 0 -> the loss does not depend on flow, so it is the same every hour."""
    loss_kW = float(pipes[pipes.built].losses.sum())
    df = pd.DataFrame([{"total_loss_kW": loss_kW, "hours": 8760,
                        "annual_loss_MWh": loss_kW * 8760 / 1000,
                        "built_length_m": float(pipes[pipes.built].length.sum())}])
    df.to_csv(path, index=False)
    return df


# --- 4.5 Maps ---------------------------------------------------------------
LAT0, LON0 = 59.3380, 18.0405
DN_COLORS = {"DN-25": "#9ecae1", "DN-30": "#4292c6", "DN-40": "#2ca25f",
             "DN-50": "#ef6c00", "DN-60": "#b2182b"}
DN_WIDTH = {"DN-25": 2.0, "DN-30": 3.0, "DN-40": 4.0, "DN-50": 5.0, "DN-60": 6.5}


PROD_LABEL_OFFSET = {"P0": (-10, 9, "right"), "P4": (-8, 12, "right"), "P3": (0, -14, "center")}
CONS_LABEL_OFFSET = {"C5": (0, 14, "center"), "C7": (8, -14, "left"), "C0": (-8, -2, "right")}


def to_m(lat, lon):
    x = (lon - LON0) * 111_320 * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * 110_574
    return x, y


def plot_dh_network(step: int, pipes: pd.DataFrame | None = None, title: str = "",
                 ax=None, show_flows: bool = True):
    """Candidate network (pipes=None) or optimised network (pipes = pipe_results)."""
    forks, prods, cons, names, pos = node_tables(step)
    xy = {k: to_m(*v) for k, v in pos.items()}
    demand_peak = selected_demand(step).max()
    if ax is None:
        fig, ax = plt.subplots(figsize=(9, 11))
    ax.set_aspect("equal")
    cand = candidate_pipes(step)
    built = set()
    if pipes is not None:
        for r in pipes[pipes.built].itertuples():
            (xa, ya), (xb, yb) = xy[r.from_key], xy[r.to_key]
            ax.plot([xa, xb], [ya, yb], color=DN_COLORS[r.hp_type],
                    lw=DN_WIDTH[r.hp_type], solid_capstyle="round", zorder=2)
            if show_flows and r.kind != "service":
                ax.text((xa + xb) / 2, (ya + yb) / 2, f"{r.design_flow_kW + r.losses:.0f}",
                        fontsize=6.5, ha="center", va="center", zorder=5,
                        bbox=dict(fc="white", ec="none", pad=0.4, alpha=0.85))
            built.add(r.id)
    for r in cand.itertuples():
        if r.id in built:
            continue
        (xa, ya), (xb, yb) = xy[r.from_key], xy[r.to_key]
        ax.plot([xa, xb], [ya, yb], color="#c4c7cc", lw=1.2,
                ls=(0, (3, 2)) if pipes is not None or r.street in ("service pipe", "producer connection") else "-",
                zorder=1)
        if pipes is None and r.street not in ("service pipe", "producer connection"):
            ax.text((xa + xb) / 2, (ya + yb) / 2, f"{r.length:.0f} m", fontsize=6.5,
                    color="#5f6368", ha="center", va="center",
                    bbox=dict(fc="white", ec="none", pad=0.4), zorder=2)
    for r in forks.itertuples():
        x, y = xy[r.key]
        ax.scatter(x, y, s=16, color="#5f6368", zorder=3)
        ax.annotate(r.key, (x, y), xytext=(3, 3), textcoords="offset points",
                    fontsize=6, color="#5f6368")
    for r in prods.itertuples():
        x, y = xy[r.key]
        ax.scatter(x, y, s=140, marker="s", color="#d93025", edgecolor="white", zorder=4)
        dx, dy, ha = PROD_LABEL_OFFSET.get(r.key, (8, -10, "left"))
        ax.annotate(f"{r.key} {r.technology.split(' (')[0]}", (x, y), xytext=(dx, dy), ha=ha,
                    textcoords="offset points", fontsize=7.5, color="#b3261e", weight="bold")
    for r in cons.itertuples():
        x, y = xy[r.key]
        pk = demand_peak[r.key]
        ax.scatter(x, y, s=25 + pk, color="#188038", edgecolor="white", zorder=4)
        dx, dy, ha = CONS_LABEL_OFFSET.get(r.key, (7, -2, "left"))
        ax.annotate(f"{r.key} {r.building_type.split(' (')[0]}\n{pk:.0f} kW", (x, y),
                    xytext=(dx, dy), ha=ha, va="center", textcoords="offset points",
                    fontsize=7, color="#137333")
    if pipes is not None:
        used = [t for t in PIPE_TYPES_BY_STEP[step] if t in set(pipes[pipes.built].hp_type)]
        handles = [plt.Line2D([], [], color=DN_COLORS[t], lw=DN_WIDTH[t], label=t) for t in used]
        handles.append(plt.Line2D([], [], color="#c4c7cc", lw=1.2, ls=(0, (3, 2)), label="not built"))
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False, title="Pipe type")
    xmin, xmax = ax.get_xlim()
    ymin, _ = ax.get_ylim()
    ax.plot([xmin + 20, xmin + 120], [ymin + 20, ymin + 20], color="black", lw=2)
    ax.text(xmin + 70, ymin + 30, "100 m", ha="center", fontsize=8)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("metres east")
    ax.set_ylabel("metres north")
    ax.grid(alpha=0.15)
    return ax


# =============================================================================
# 5. oemof HELPERS  (Step 3, 4, 5 use the SAME model -> consistent comparison)
# =============================================================================
def build_and_solve_heat_system(data, tech, carriers, demand_col="heat demand",
                                co2_price=0.0, include_fuel_costs=True,
                                include_var_om=True, i=0.05):
    """Build and solve the 5-producer district-heating supply model in oemof.solph.

    Written in the style of the oemof tutorial 'district_heating_supply_2'.

    data     : hourly DataFrame (MW, EUR/MWh) with columns
               demand_col, 'gas price', 'el_spot_price', 'solar_cf'
    tech     : DataFrame indexed by unit label (see Step 3) with columns
               efficiency, capex_eur_per_MW, var_om_eur_per_MWh, max_capacity_MW,
               lifetime_yr, input  (input = carrier name in `carriers`)
    carriers : DataFrame indexed by carrier with column 'ef_tCO2_per_MWh'
               (and 'price_eur_per_MWh' for carriers with a constant price)
    co2_price          : EUR/tCO2, added to each unit's variable cost (Step 5)
    include_fuel_costs : False -> all fuel/electricity prices = 0   (Step 5, scenario B)
    include_var_om     : False -> all variable O&M costs = 0        (Step 5, scenario B)

    Decision variables : installed capacity of each unit [MW] (solph.Investment)
                         hourly heat output of each unit [MW]
    Objective          : sum annualised capex + fuel + var O&M (+ CO2) [EUR/yr]
    Constraints        : hourly heat balance on 'heat network' bus,
                         output <= capacity (solar: <= capacity * solar_cf),
                         capacity <= max_capacity_MW, input = output / efficiency
    Returns: (model, results, parts)  parts = dict with buses/components for post-processing
    """
    from oemof.solph import Bus, EnergySystem, Flow, Investment, Model, processing
    from oemof.solph.components import Converter, Sink, Source

    f = 1.0 if include_fuel_costs else 0.0
    om = 1.0 if include_var_om else 0.0

    def ep_costs(unit):
        t = tech.loc[unit]
        return epc(t["capex_eur_per_MW"], i=i, n=t["lifetime_yr"])

    def max_cap(unit):
        m = tech.loc[unit, "max_capacity_MW"]
        return float("inf") if pd.isna(m) else float(m)

    def var_cost(unit):
        """var O&M + CO2 cost per MWh heat: co2_price * EF_input / efficiency."""
        t = tech.loc[unit]
        ef = carriers.loc[t["input"], "ef_tCO2_per_MWh"]
        return om * t["var_om_eur_per_MWh"] + co2_price * ef / t["efficiency"]

    # infer_last_interval=True: with an hourly index of 8760 time stamps oemof then models 8760
    # one-hour intervals. (The tutorial uses False, which silently drops the last hour of the year.)
    district_heating_system = EnergySystem(timeindex=data.index, infer_last_interval=True)

    # Buses
    heat_bus = Bus(label="heat network")
    gas_bus = Bus(label="gas network")
    biomass_bus = Bus(label="biomass network")
    electricity_bus = Bus(label="electricity network")
    waste_heat_bus = Bus(label="waste heat network")
    district_heating_system.add(heat_bus, gas_bus, biomass_bus, electricity_bus, waste_heat_bus)

    # Sources (fuel prices)
    gas_source = Source(label="gas source",
                        outputs={gas_bus: Flow(variable_costs=f * data["gas price"])})
    biomass_source = Source(label="biomass source",
                            outputs={biomass_bus: Flow(variable_costs=f * carriers.loc["biomass", "price_eur_per_MWh"])})
    electricity_source = Source(label="electricity source",
                                outputs={electricity_bus: Flow(variable_costs=f * data["el_spot_price"])})
    waste_heat_source = Source(label="waste heat source", outputs={waste_heat_bus: Flow()})
    district_heating_system.add(gas_source, biomass_source, electricity_source, waste_heat_source)

    # Heat demand sink (fixed profile)
    heat_sink = Sink(label="heat sink",
                     inputs={heat_bus: Flow(nominal_capacity=data[demand_col].max(),
                                            fix=data[demand_col] / data[demand_col].max())})
    district_heating_system.add(heat_sink)

    # Build one component for every technology row (supports multiple heat pumps).
    unit_parts = {}
    carrier_buses = {"gas": gas_bus, "biomass": biomass_bus, "electricity": electricity_bus}
    for unit, t in tech.iterrows():
        output = Flow(nominal_capacity=Investment(
            ep_costs=ep_costs(unit), maximum=max_cap(unit)),
            variable_costs=var_cost(unit))
        kind = t.get("technology_type", "")
        if t["input"] == "solar":
            output.max = data["solar_cf"]
            comp = Source(label=unit, outputs={heat_bus: output})
            in_bus, price = None, pd.Series(0.0, index=data.index)
        else:
            in_bus = carrier_buses[t["input"]]
            price = (data["gas price"] if t["input"] == "gas" else
                     data["el_spot_price"] if t["input"] == "electricity" else
                     pd.Series(carriers.loc[t["input"], "price_eur_per_MWh"], index=data.index))
            if kind == "heat_pump" or (t["input"] == "electricity" and t["efficiency"] > 1):
                cop = float(t["efficiency"])
                if cop <= 1:
                    raise ValueError(f"{unit}: heat-pump COP must exceed 1")
                # Independent source buses keep the station and supermarket separate.
                source_bus = Bus(label=f"{unit} heat source bus")
                source = Source(label=f"{unit} heat source", outputs={source_bus: Flow()})
                district_heating_system.add(source_bus, source)
                comp = Converter(label=unit,
                    inputs={electricity_bus: Flow(), source_bus: Flow()},
                    outputs={heat_bus: output},
                    conversion_factors={electricity_bus: 1 / cop,
                                        source_bus: (cop - 1) / cop, heat_bus: 1})
            else:
                comp = Converter(label=unit, inputs={in_bus: Flow()},
                    outputs={heat_bus: output},
                    conversion_factors={heat_bus: float(t["efficiency"])})
        district_heating_system.add(comp)
        unit_parts[unit] = (comp, in_bus, price)

    model = Model(district_heating_system)
    model.solve(solver=SOLVER, solve_kwargs={"tee": False}, cmdline_options=SOLVER_OPTIONS)
    results = processing.results(model)
    parts = dict(heat_bus=heat_bus, heat_sink=heat_sink, units=unit_parts,
        index=data.index,
        settings=dict(co2_price=co2_price, include_fuel_costs=include_fuel_costs,
                      include_var_om=include_var_om, i=i))
    return model, results, parts


def summarize_units(results, parts, tech, carriers):
    """Per-unit table: capacity, heat, full-load hours, hours at max capacity,
    fuel/O&M/CO2 costs and emissions. All costs in EUR/yr, energy in MWh/yr.
    'fuel cost' and 'CO2 cost' always use the REAL prices so scenarios can be
    compared on the same economic basis (the optimisation itself may ignore them)."""
    s = parts["settings"]
    rows, heat_bus = {}, parts["heat_bus"]
    for unit, (comp, in_bus, price) in parts["units"].items():
        heat = results[(comp, heat_bus)]["sequences"]["flow"].reindex(parts["index"])
        cap = results[(comp, heat_bus)]["scalars"]["invest"]
        cap = cap if cap > 1e-9 else 0.0   # clip solver round-off (e.g. -0.0)
        fuel_in = (results[(in_bus, comp)]["sequences"]["flow"].reindex(parts["index"])
                   if in_bus is not None else heat * 0)
        ef = carriers.loc[tech.loc[unit, "input"], "ef_tCO2_per_MWh"]
        co2 = (fuel_in * ef).sum()
        rows[unit] = {
            "capacity [MW]": cap,
            "heat [MWh]": heat.sum(),
            "input energy [MWh]": fuel_in.sum(),   # fuel or electricity (waste heat / sun not counted)
            "share of heat [%]": 0.0,
            "full-load hours [h]": heat.sum() / cap if cap > 1e-6 else 0,
            "hours at max capacity [h]": int((heat >= 0.999 * cap).sum()) if cap > 1e-6 else 0,
            "annualised capex [EUR]": cap * epc(tech.loc[unit, "capex_eur_per_MW"], i=s["i"], n=tech.loc[unit, "lifetime_yr"]),
            "fuel/electricity cost [EUR]": (fuel_in * price).sum(),
            "var O&M cost [EUR]": heat.sum() * tech.loc[unit, "var_om_eur_per_MWh"],
            "CO2 [t]": co2,
            "CO2 cost [EUR]": co2 * s["co2_price"],
        }
    df = pd.DataFrame(rows).T
    df["share of heat [%]"] = 100 * df["heat [MWh]"] / df["heat [MWh]"].sum()
    df["total cost [EUR]"] = df[["annualised capex [EUR]", "fuel/electricity cost [EUR]",
                                 "var O&M cost [EUR]", "CO2 cost [EUR]"]].sum(axis=1)
    df.loc["TOTAL"] = df.sum()
    df.loc["TOTAL", ["full-load hours [h]", "hours at max capacity [h]"]] = np.nan
    return df


def heat_dispatch(results, parts):
    """Hourly heat output per unit [MW] as a DataFrame (+ demand column)."""
    heat_bus = parts["heat_bus"]
    idx = parts["index"]
    df = pd.DataFrame({u: results[(c, heat_bus)]["sequences"]["flow"].reindex(idx)
                       for u, (c, _, _) in parts["units"].items()})
    df["demand"] = results[(heat_bus, parts["heat_sink"])]["sequences"]["flow"].reindex(idx)
    return df


UNIT_COLORS = {"biomass boiler": "#2E7D32", "gas boiler": "#EC6707", "heat pump": "#B54036",
               "electric boiler": "#1E88E5", "solar thermal": "#FBC02D",
               "heat pump station": "#B54036", "heat pump supermarket": "#8E44AD"}


def plot_dispatch(dispatch, title):
    """Stacked hourly heat production (daily means for readability) + duration curves."""
    fig, axs = plt.subplots(2, 1, figsize=(14, 9))
    units = [c for c in dispatch.columns if c != "demand"]
    daily = dispatch.resample("D").mean() if isinstance(dispatch.index, pd.DatetimeIndex) else dispatch
    axs[0].stackplot(daily.index, *[daily[u] for u in units], labels=units,
                     colors=[UNIT_COLORS.get(u) for u in units])
    axs[0].plot(daily.index, daily["demand"], "k", lw=1, label="demand")
    axs[0].set_ylabel("Heat [MW] (daily mean)")
    axs[0].set_title(title)
    axs[0].legend(loc="upper center", ncol=6)
    for u in units:
        axs[1].plot(np.sort(dispatch[u].values)[::-1], label=u, color=UNIT_COLORS.get(u))
    axs[1].set_xlabel("Hours of the year (sorted)")
    axs[1].set_ylabel("Heat [MW]")
    axs[1].set_title("Duration curves per unit")
    axs[1].legend()
    plt.tight_layout()
    return fig

"""
helpers.py - the ONLY supporting code file of the project.

Everything else lives in the notebooks (step1 ... step5). This file holds code
that more than one notebook needs, so that every step uses *exactly* the same
functions:

    0. Project-wide assumptions           (used by all notebooks - change them HERE only)
    1. Paths + solver selection           (used by all notebooks)
    2. Economics: epc, LCOH               (copied 1:1 from the OEMOF tutorial helpers.py)
    3. Demand data loader                 (Step 1, 2, 3)
    4. DHNx helpers: pipe capacity, CSV writer, network plot   (Step 1, 2)
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
    """Return the 50 hourly heat-demand profiles (8760 rows x 50 columns, kW).

    The Excel file is slow to read (~30 s), so the first call caches Sheet1 as
    data/demand_profiles.csv and later calls read the CSV instead.
    Column 'Unnamed: 18' in the Excel (missing header) is renamed 'Load 18'.
    """
    cache = DATA / "demand_profiles.csv"
    if cache.exists():
        return pd.read_csv(cache, index_col="hour")

    df = pd.read_excel(DATA / "Demand profiles_heating.xlsx", sheet_name="Sheet1")
    df = df.rename(columns={"Unnamed: 18": "Load 18"}).drop(columns="Time")
    df.index = pd.RangeIndex(len(df), name="hour")
    df.to_csv(cache)
    return df


# =============================================================================
# 4. DHNx HELPERS
# =============================================================================
def pipe_capacity_kW(dn_mm, v_max, delta_T, rho=978.0, cp=4.19):
    """Max heat transport of a pipe:  P = rho * v * (pi d^2 / 4) * cp * dT  [kW].

    dn_mm  : inner diameter in mm (approximated by the DN number)
    v_max  : max flow velocity in m/s (limits pressure drop / noise)
    delta_T: supply - return temperature in K
    rho    : water density [kg/m3] at ~70 degC,  cp: [kJ/(kg K)]
    """
    d = dn_mm / 1000
    m_dot = rho * v_max * np.pi * d**2 / 4  # kg/s
    return m_dot * cp * delta_T  # kW


def _node_xy(node, consumers, producers, forks):
    kind, idx = node.split("-", 1)
    table = {"consumers": consumers, "producers": producers, "forks": forks}[kind]
    row = table.set_index("id").loc[int(idx)]
    return row["lon"], row["lat"]


def write_dhnx_input(folder, consumers, producers, forks, pipes, pipe_types):
    """Write the DHNx input folders exactly like the DHNx tutorial:

    folder/
      twn_data/     consumers.csv, producers.csv, forks.csv, pipes.csv
      invest_data/  network/pipes.csv, consumers/{bus,demand}.csv, producers/{bus,source}.csv

    Pipe lengths are calculated from the node coordinates (straight line, m),
    so moving a node in the notebook automatically updates the lengths.
    Returns (twn_data_path, invest_data_path).
    """
    folder = Path(folder)
    twn, inv = folder / "twn_data", folder / "invest_data"
    for p in [twn, inv / "network", inv / "consumers", inv / "producers"]:
        p.mkdir(parents=True, exist_ok=True)

    pipes = pipes.copy()
    pipes["length"] = [
        round(float(np.hypot(*np.subtract(_node_xy(a, consumers, producers, forks),
                                          _node_xy(b, consumers, producers, forks)))), 1)
        for a, b in zip(pipes["from_node"], pipes["to_node"])
    ]

    # --- town / network data (twn_data) -------------------------------------
    consumers.to_csv(twn / "consumers.csv", index=False)
    producers.to_csv(twn / "producers.csv", index=False)
    forks.to_csv(twn / "forks.csv", index=False)
    pipes.to_csv(twn / "pipes.csv", index=False)

    # --- investment options (invest_data) -----------------------------------
    pipe_types.to_csv(inv / "network" / "pipes.csv", index=False)
    pd.DataFrame([{"label_2": "heat", "active": 1, "excess": 0, "shortage": 0,
                   "shortage costs": 999999, "excess costs": 9999}]
                 ).to_csv(inv / "consumers" / "bus.csv", index=False)
    pd.DataFrame([{"label_2": "heat", "active": 1, "nominal_capacity": 1}]
                 ).to_csv(inv / "consumers" / "demand.csv", index=False)
    pd.DataFrame([{"label_2": "heat", "active": 1, "excess": 0, "shortage": 0,
                   "shortage costs": 9999, "excess costs": 9999}]
                 ).to_csv(inv / "producers" / "bus.csv", index=False)
    pd.DataFrame([{"label_2": "heat", "active": 1}]
                 ).to_csv(inv / "producers" / "source.csv", index=False)
    return twn, inv


def plot_network(network, title, show_dn=False, ax=None):
    """Tutorial plot: StaticMap + coloured consumers/producers/forks.
    show_dn=True writes the chosen pipe type (hp_type) on each pipe, like the
    DHNx 'discrete_DN_numbers' example."""
    import dhnx

    if ax is None:
        _, ax = plt.subplots(figsize=(9, 7))
    plt.sca(ax)
    static_map = dhnx.plotting.StaticMap(network)
    static_map.draw(background_map=False)
    plt.title(title)
    c = network.components
    plt.scatter(c.consumers["lon"], c.consumers["lat"], color="tab:green", label="consumers", zorder=2.5, s=60)
    plt.scatter(c.producers["lon"], c.producers["lat"], color="tab:red", label="producers", zorder=2.5, s=80)
    plt.scatter(c.forks["lon"], c.forks["lat"], color="tab:grey", label="forks", zorder=2.5, s=30)
    for kind, prefix in [("consumers", "C"), ("producers", "P")]:
        for i, r in c[kind].iterrows():
            plt.text(r["lon"] + 6, r["lat"] + 6, f"{prefix}{i}", fontsize=10)

    if show_dn and "hp_type" in c.pipes.columns:
        for _, p in c.pipes.iterrows():
            xy = [_lonlat(c, p[n]) for n in ("from_node", "to_node")]
            plt.text((xy[0][0] + xy[1][0]) / 2, (xy[0][1] + xy[1][1]) / 2, p["hp_type"],
                     va="center", ha="center", fontsize=8,
                     bbox=dict(facecolor="white", edgecolor="none", alpha=0.8))
    plt.legend(loc=(1.01, 0.7))
    plt.xlabel("x [m]")
    plt.ylabel("y [m]")
    return ax


def _lonlat(components, node):
    kind, idx = node.split("-", 1)
    table = components[kind]
    row = table.loc[int(idx)] if int(idx) in table.index else table.loc[idx]
    return row["lon"], row["lat"]


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
    Objective          : sum annualised capex (epc) + fuel + var O&M (+ CO2) [EUR/yr]
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

    # Unit 1: biomass boiler
    biomass_boiler = Converter(
        label="biomass boiler",
        inputs={biomass_bus: Flow()},
        outputs={heat_bus: Flow(nominal_capacity=Investment(ep_costs=ep_costs("biomass boiler"),
                                                            maximum=max_cap("biomass boiler")),
                                variable_costs=var_cost("biomass boiler"))},
        conversion_factors={heat_bus: tech.loc["biomass boiler", "efficiency"]})

    # Unit 2: gas boiler
    gas_boiler = Converter(
        label="gas boiler",
        inputs={gas_bus: Flow()},
        outputs={heat_bus: Flow(nominal_capacity=Investment(ep_costs=ep_costs("gas boiler"),
                                                            maximum=max_cap("gas boiler")),
                                variable_costs=var_cost("gas boiler"))},
        conversion_factors={heat_bus: tech.loc["gas boiler", "efficiency"]})

    # Unit 3: heat pump (electricity + free waste heat -> heat), as in the tutorial
    cop = tech.loc["heat pump", "efficiency"]
    heat_pump = Converter(
        label="heat pump",
        inputs={electricity_bus: Flow(), waste_heat_bus: Flow()},
        outputs={heat_bus: Flow(nominal_capacity=Investment(ep_costs=ep_costs("heat pump"),
                                                            maximum=max_cap("heat pump")),
                                variable_costs=var_cost("heat pump"))},
        conversion_factors={electricity_bus: 1 / cop, waste_heat_bus: (cop - 1) / cop})

    # Unit 4: electric boiler
    electric_boiler = Converter(
        label="electric boiler",
        inputs={electricity_bus: Flow()},
        outputs={heat_bus: Flow(nominal_capacity=Investment(ep_costs=ep_costs("electric boiler"),
                                                            maximum=max_cap("electric boiler")),
                                variable_costs=var_cost("electric boiler"))},
        conversion_factors={heat_bus: tech.loc["electric boiler", "efficiency"]})

    # Unit 5: solar thermal collector (output <= capacity * hourly capacity factor)
    solar_thermal = Source(
        label="solar thermal",
        outputs={heat_bus: Flow(nominal_capacity=Investment(ep_costs=ep_costs("solar thermal"),
                                                            maximum=max_cap("solar thermal")),
                                max=data["solar_cf"],
                                variable_costs=var_cost("solar thermal"))})

    district_heating_system.add(biomass_boiler, gas_boiler, heat_pump, electric_boiler, solar_thermal)

    # Build and solve
    model = Model(district_heating_system)
    model.solve(solver=SOLVER, solve_kwargs={"tee": False}, cmdline_options=SOLVER_OPTIONS)
    results = processing.results(model)

    parts = dict(
        heat_bus=heat_bus, heat_sink=heat_sink,
        units={  # label: (component, input bus or None, fuel price series [EUR/MWh_input])
            "biomass boiler": (biomass_boiler, biomass_bus,
                               pd.Series(carriers.loc["biomass", "price_eur_per_MWh"], index=data.index)),
            "gas boiler": (gas_boiler, gas_bus, data["gas price"]),
            "heat pump": (heat_pump, electricity_bus, data["el_spot_price"]),
            "electric boiler": (electric_boiler, electricity_bus, data["el_spot_price"]),
            "solar thermal": (solar_thermal, None, pd.Series(0.0, index=data.index)),
        },
        index=data.index,
        settings=dict(co2_price=co2_price, include_fuel_costs=include_fuel_costs,
                      include_var_om=include_var_om, i=i),
    )
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
               "electric boiler": "#1E88E5", "solar thermal": "#FBC02D"}


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

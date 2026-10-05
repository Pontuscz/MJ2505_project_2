# MJ2505 Project 2 – District Heating Network Optimisation

A district heating network designed with **DHNx** (Steps 1–2), whose heat supply is then optimised with **oemof.solph** (Steps 3–5).
There is one notebook per project step. Run them **in order, top to bottom**: each step reads what the previous one saved.

## Quick start

1. Clone the repo and open the folder in VS Code (or JupyterLab).
2. Open `step1_base_network.ipynb` and run the first code cell (**0.1 Environment setup**). On a new computer it:
   - creates `.venv/` with Python 3.11–3.14 and installs `requirements.txt`
   - registers the Jupyter kernel **"Python (MJ2505 DH project)"**
   - asks you to switch to that kernel. Do so, restart the kernel, then run again. From then on the cell just prints ✅.
3. Run the five notebooks in order. Running all of them takes about 2 minutes.

**Solver:** if **CBC** is installed (the course setup is `conda install -c conda-forge coin-or-cbc`), it is used automatically. If not, the pip-installed **HiGHS** solver is used instead, so conda is not required. Both reach the same optimum. See `SOLVER` in `helpers.py`.

## Repository structure

```
MJ2505_DH_project/
├── README.md
├── requirements.txt                       pinned package versions (installed by the setup cell)
├── helpers.py                             the ONLY supporting code file (shared by all notebooks)
│
├── step1_base_network.ipynb               DHNx: 5 consumers, 2 producers, DN-30/DN-50      (grade E)
├── step2_network_expansion.ipynb          DHNx: 10 consumers, 5 producers, 5 DN types       (grade D)
├── step3_oemof_dispatch.ipynb             oemof: capacity + dispatch of the 5 producers     (grade C)
├── step4_dhnx_oemof_integration.ipynb     oemof: demand + DHNx network losses               (grade B)
├── step5_environmental_optimization.ipynb oemof: CO2 cost scenarios A/B + sensitivity       (grade A)
│
├── data/                                  INPUTS
│   ├── Demand profiles_heating.xlsx         given: 50 hourly heat-demand profiles
│   ├── input_data_DH1.csv                   PLACEHOLDER prices (oemof tutorial, 2019), replace
│   └── stepN/                               written by notebook N, read by later steps
└── results/                               OUTPUTS (tables + figures for the report)
    └── stepN/
```

## Data flow between the steps

```
Excel demand ──► Step 1 ──► data/step1/ (twn_data, invest_data) ─┐
                    └────► results/step1/summary.csv ─────────────┤
                                                                  ▼
                 Step 2 ──► data/step2/twn_data/consumers.csv ──► Step 3 (which 10 profiles)
                    └────► results/step2/network_losses.csv ───► Step 4 (Q_total = Q_demand + Q_losses)
                 Step 3 ──► data/step3/ (oemof_input, technology_parameters, carriers) ──► Step 4, 5
                 Step 4 ──► data/step4/oemof_input_with_losses.csv ──► Step 5
```

## Notebook conventions

- Each code cell has a markdown cell above it that explains what the cell does and what the group has to decide.
- 🔧 **ASSUMPTION** marks a value that must be stated, given units and justified (source or engineering reasoning) in the report.
- ✏️ **TODO** marks a group decision or an answer to write.
- Every notebook ends with the **questions from the project description**, with hints pointing to the relevant result cells.
- Code follows the tutorials (`DHNx Tutorial/DHNx.ipynb`, `OEMOF Tutorial/oemof.ipynb`): the same calls (`ThermalNetwork().from_csv_folder`, `load_invest_options`, `optimize_investment`, `Bus/Source/Converter/Sink/Flow/Investment`, `epc`, `LCOH`) and the same variable names.
- Units: DHNx uses kW, m and € (Steps 1–2). oemof uses MW, MWh and € (Steps 3–5). The kW → MW conversion happens once, in Step 3, and is checked again in Step 4.

## What's in `helpers.py`

| Section | Contents | Used by |
|---|---|---|
| 0 | Project-wide assumptions: country, latitude, supply/return temperature, interest rate, pipe lifetime. Change them **only here** | all |
| 1 | Paths, solver choice (CBC → HiGHS fallback) | all |
| 2 | `epc()`, `LCOH()`, copied unchanged from the OEMOF tutorial `helpers.py` | all |
| 3 | `load_demand_profiles()`: reads the Excel once and caches it as CSV | 1, 2, 3 |
| 4 | `pipe_capacity_kW()`, `write_dhnx_input()` (writes the tutorial folder structure), `plot_network()` | 1, 2 |
| 5 | `build_and_solve_heat_system()`: the oemof model of the 5 producers, written in tutorial style. Steps 3, 4 and 5 all call this function, so they solve the **same** model. Also `summarize_units()`, `heat_dispatch()`, `plot_dispatch()` | 3, 4, 5 |

## Placeholders to replace before the report

All of these are marked 🔧 or ✏️ in the notebooks. They exist so the full chain runs from day one.

- Country, temperatures, interest rate, lifetimes (`helpers.py` section 0)
- Choice of the 5 + 5 demand profiles and the network layout and coordinates (Step 1 §2 and §4, Step 2 §1 and §4)
- Pipe costs, heat losses and velocity limits per DN (Step 1 §3, Step 2 §2)
- Producer technologies, capacities and marginal costs in DHNx (Step 1 §4, Step 2 §3)
- **Price time series** in `data/input_data_DH1.csv`: these are the tutorial's 2019 prices, so replace them with prices for your country (Step 3 §2)
- Solar profile clearness indices, or replace the profile with PVGIS data (Step 3 §3)
- Technology data and sources, preferably the Danish Energy Agency catalogue (Step 3 §5)
- CO₂ price and emission factors, plus the sensitivity parameter (Step 5 §2, §5)

## Modelling notes (useful for the report and the oral exam)

- **DHNx design hour:** DHNx sizes the network for the peak hour (`heat_demand='scalar'`, every consumer at `P_heat_max`). Pipe investment is annualised with `epc()`. Producer operating cost is entered as €/MWh × full-load hours so that it is comparable with the annual pipe cost.
- **Losses:** losses are flow-independent (`l_factor_fix`, W/m). The DHNx loss in kW is therefore the same in every hour, so it is added as a constant hourly profile in Step 4.
- **Hourly index:** oemof uses `infer_last_interval=True`, so all 8760 hours are modelled. The tutorial's `False` silently drops the last hour.
- **"Both directions" warnings:** with HiGHS, DHNx may log `Investment status ... is 1 for both directions`. This is solver round-off (binary values such as 1 + 1e-14), not a real double build.

## Git tips

- Commit the notebooks **without outputs**, so diffs stay small and merges are easier (VS Code: *Clear All Outputs*, or use `nbstripout`).
- Avoid editing the same notebook at the same time as a teammate. Split work by step.
- `.venv/` and the demand CSV cache are ignored. `results/` is tracked, so everyone sees the latest figures.

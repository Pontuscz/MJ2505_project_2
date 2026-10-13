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
│   ├── pipe_types.csv                       pipe table DN-25 ... DN-60 (Didrik), DHNx format
│   ├── producers_technology.csv             technology data of P0-P4 (Elisabeth), for Step 3
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
| 0 | also `DEMAND_SCALE` (0.4, see below) and `SIMULTANEITY` | all |
| 3 | `load_demand_profiles()`: reads the Excel once, caches it as CSV, fixes the two data gaps. `demand_scaled()`: the demand every step must use | 1–3 |
| 4 | Vasastan network (real coordinates around Odenplan): consumers, producers, junctions, candidate streets. `write_step_inputs()` (tutorial folder structure), `run_dhnx()` (adds the two rules below), `pipe_results()`, `producer_supply()`, `summary()`, `export_network_losses()`, `plot_dh_network()` | 1, 2 |
| 5 | `build_and_solve_heat_system()`: the oemof model of the 5 producers, written in tutorial style. Steps 3, 4 and 5 all call this function, so they solve the **same** model. Also `summarize_units()`, `heat_dispatch()`, `plot_dispatch()` | 3, 4, 5 |

## Placeholders to replace before the report

All of these are marked 🔧 or ✏️ in the notebooks. They exist so the full chain runs from day one.

- Country, temperatures, interest rate, lifetimes (`helpers.py` section 0)
- ~~Demand profiles, network layout, pipe data, producer sites~~ **done in Steps 1–2** (Vasastan network, Didrik's pipe table)
- **Step 3 technology table:** still the scaffold placeholders (biomass, gas boiler, heat pump, electric boiler, solar). Replace with the five Step 2 producers and Elisabeth's data in `data/producers_technology.csv`: P0 heat pump (station air), P1 biomass boiler, P2 electric boiler, P3 biogas boiler, P4 heat pump (supermarket)
- **Price time series** in `data/input_data_DH1.csv`: these are the tutorial's 2019 prices, so replace them with prices for your country (Step 3 §2)
- Solar profile clearness indices, or replace the profile with PVGIS data (Step 3 §3)
- Technology data and sources, preferably the Danish Energy Agency catalogue (Step 3 §5)
- CO₂ price and emission factors, plus the sensitivity parameter (Step 5 §2, §5)

## Modelling notes (useful for the report and the oral exam)

- **Demand scale 0.4:** all Excel profiles are used as building-type shapes and multiplied by `DEMAND_SCALE` = 0.4, in every step. The assignment fixes the pipe range to DN-25 ... DN-60, and Didrik's capacities (checked against ΔT = 40 K and about 100 Pa/m) give at most 208.9 kW for DN-50. Unscaled, the five Step 1 consumers need 873 kW from two producers that each connect through one pipe, which is infeasible (tested). Alternative if the group prefers real-size buildings: about 2× the pipe capacities (ΔT 50 K, 250 Pa/m) plus simultaneity 0.72/0.77. That also solves, but it changes Didrik's table.
- **DHNx design hour:** DHNx sizes the network for the peak hour (`heat_demand='scalar'`, every consumer at its scaled `P_heat_max`, simultaneity 1). Pipe cost is the overnight investment in €/m. Producers are free inside DHNx, so annualising with `epc()` would scale every pipe cost by the same factor and give the same design.
- **Two rules added to DHNx** (`helpers.py` section 4, explained in Step 1 §4):
  - **One pipe type per street:** DHNx 0.0.4 otherwise stacks two sizes in parallel, and its own result code then crashes.
  - **One connected network:** without it, free heat makes DHNx build one island per producer. Steps 3–5 assume all producers serve the total demand. The cost premium is +1.6 % in Step 1 and +28 % in Step 2. Both notebooks show the island version for comparison.
- **Producer supply in DHNx is not dispatch:** it is decided by pipe costs only. In Step 2 the electric boiler delivers about 0 kW at the design hour, and the biogas and biomass boilers are capped by their connection pipes. The economic split comes from oemof in Step 3.
- **Losses:** losses are flow-independent (`l_factor_fix`, W/m). The DHNx loss in kW is therefore the same in every hour, so it is added as a constant hourly profile in Step 4.
- **Hourly index:** oemof uses `infer_last_interval=True`, so all 8760 hours are modelled. The tutorial's `False` silently drops the last hour.
- **CBC status read (fixed):** Steps 3–4 read the solver status as a plain dict, which only works with HiGHS. They now handle both solvers. The optional fixed-capacity run in Step 4 §4.2 now reports "infeasible" instead of crashing "run all": the Step 3 plant is sized exactly to the peak, so it cannot also cover the losses.
- **"Both directions" warnings:** with HiGHS, DHNx may log `Investment status ... is 1 for both directions`. This is solver round-off (binary values such as 1 + 1e-14), not a real double build.

## Git tips

- Commit the notebooks **without outputs**, so diffs stay small and merges are easier (VS Code: *Clear All Outputs*, or use `nbstripout`).
- Avoid editing the same notebook at the same time as a teammate. Split work by step.
- `.venv/` and the demand CSV cache are ignored. `results/` is tracked, so everyone sees the latest figures.

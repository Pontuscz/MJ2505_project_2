# Vasastan network: topology and demand (Steps 1 and 2)

Real street layout around Odenplan, Vasastan, Stockholm. All coordinates are WGS84 latitude/longitude from Google Maps (street intersections and building addresses). `build_network.py` regenerates every file in this folder from the Excel file.

## Files

| File | Used by | Content |
|---|---|---|
| `step1/twn_data/` | DHNx, Step 1 | `consumers.csv`, `producers.csv`, `forks.csv`, `pipes.csv` |
| `step2/twn_data/` | DHNx, Step 2 | Same, expanded to 10 consumers and 5 producers |
| `step1/demand_selected.csv`, `step2/demand_selected.csv` | oemof, Step 3 | Hourly demand of the selected consumers (kW), 8760 rows |
| `map_step1.png`, `map_step2.png` | Report | Candidate network maps |
| `network_summary.csv` | Report | Peaks, coincidence factor, annual demand, pipe lengths |

The `invest_data` folder (pipe types, costs, losses, producer capacities) is not here. That is Didrik's part.

DHNx only needs `id, lat, lon, P_heat_max` (consumers), `id, lat, lon` (producers and forks) and `id, from_node, to_node, length` (pipes). The extra columns (`name`, `street`, `profile` and so on) are for readability. If DHNx complains about them, delete them.

## Topology

Two parallel north-south streets (Dalagatan, Västmannagatan) are joined by four east-west streets (Vanadisvägen, Frejgatan, Karlbergsvägen, Odengatan). This ladder gives 3 loops in Step 1 and 6 in Step 2, so DHNx has real route choices. It picks the cheapest subset of streets that connects everyone. It would not have that choice if every building had only one path to a producer.

- **Pipe lengths:** straight-line distance between consecutive intersections. This works because the streets are straight between junctions. Service pipes are measured from the building's address point to the nearest intersection.
- **Simplification:** buildings connect at an intersection rather than mid-block. This makes service pipes slightly longer than in reality.
- **Step 2:** extends the network east with Upplandsgatan and the Vanadisvägen extension. The Step 1 nodes are unchanged.

## Producers

| Id | Technology | Site | Real or assumed |
|---|---|---|---|
| P0 | Heat pump on station exhaust air | Odenplan commuter rail station (Citybanan), Dalagatan entrance | Real station; heat recovery from ventilation is an assumption (open district heating concept) |
| P1 | Biomass (pellet) boiler | Sabbatsberg hospital campus | **Assumed site.** Chosen for truck access from Dalagatan and distance from homes |
| P2 (Step 2) | Electric boiler | Same plant as P0 | **Assumed.** Shares the grid connection with the heat pump |
| P3 (Step 2) | Biogas boiler | Odengatan 69 | **Assumed boiler room.** Stockholm's inner-city gas grid supplies biogas |
| P4 (Step 2) | Heat pump on supermarket refrigeration heat | Hemköp, Karlbergsvägen 24 | Real supermarket; heat recovery is an assumption |

The producers sit at different ends of the network (north, south and later east and centre). Each producer is fed into the network at a different point. That is why pipes near producers carry large flows and get large diameters, while end branches get small ones.

## Demand selection

The Excel file has only **6 independent profiles** (Loads 1–6). Every other column is a formula, either one of these plus a constant (Load 7 = Load 1 + 50) or a scaled copy (Loads 12, 18, 30, 36, 42 and 48 are linear scalings of Load 1).

- **Step 1:** uses five different shapes, Loads 2–6, so the consumer peaks do not coincide.
- **Step 2:** adds derived profiles.
- **Load 1 itself is avoided:** its 2.6 MW peak does not fit DN-25 to DN-60 pipes.

| Id | Building (real address) | Profile | Peak kW | MWh/year | Why this profile fits |
|---|---|---|---|---|---|
| C0 | Vasa Real school, Karlbergsvägen 15 | Load 6 | 258 | 559 | Winter peak, low summer use |
| C1 | ibis Styles Odenplan hotel, Västmannagatan 61 | Load 5 | 182 | 1045 | Weak seasonality: hot-water dominated |
| C2 | Sabbatsbergs sjukhus, Olivecronas väg 1 | Load 3 | 273 | 614 | Flat all year: 24/7 operation |
| C3 | Residential block, Dalagatan 46 | Load 4 | 109 | 176 | Strong winter peak: space heating |
| C4 | Residential block, Västmannagatan 79 | Load 2 | 50 | 151 | Strong winter peak, small building |
| C5 | Sabbatsbergsbyn care home, Eastmansvägen 29 | Load 11 | 212 | 1308 | Load 5 + 30: 24/7 with hot water |
| C6 | Matteusskolan, Vanadisvägen 16 | Load 18 | 458 | 1740 | Scaled Load 1: large winter-peaked building |
| C7 | Residential block, Odengatan 75 | Load 10 | 129 | 351 | Load 4 + 20 |
| C8 | Residential block, Frejgatan 34 | Load 16 | 149 | 526 | Load 4 + 40 |
| C9 | Residential block, Upplandsgatan 82 | Load 22 | 169 | 702 | Load 4 + 60 |

**Key numbers:**

| | Step 1 | Step 2 |
|---|---|---|
| Sum of individual peaks | 873 kW | 1990 kW |
| Coincident peak (max of hourly sum) | 625 kW | 1531 kW |
| Coincidence factor | 0.72 | 0.77 |
| Annual demand | 2544 MWh | 7171 MWh |
| Candidate pipe length | 2147 m | 3808 m |

## Assumptions to state in the report

1. **Units:** the Excel file states none. Values are read as hourly mean power in kW, which gives realistic annual totals per building.
2. **`P_heat_max`:** set to each profile's annual peak. With DHNx simultaneity 1, the network is sized for all peaks at once (873 kW), which is conservative. The data gives a coincidence factor of 0.72 (0.77 in Step 2), which can be used to justify a lower simultaneity value. Check the exact setting name in DHNx.
3. **Building matching:** profiles are matched to buildings by shape, not by metered data. Absolute sizes are taken from the dataset. A whole hospital uses more than 614 MWh per year, so C2 represents one building on the campus.
4. **Data fixes:**
   - Load 6 is zero for the last 24 hours (31 Dec), a data gap. Those hours are filled with 30 Dec.
   - The column after Load 17 has no header. By position it is Load 18.
5. **Derived profiles:** they add a flat base load to an original shape. This raises full-load hours (for example 4153 h for Load 22). It is a property of the dataset, not a modelling choice.
6. **Sites:** P1, P2 and P3 sites, and the Sabbatsberg campus junction F5, are assumed. Everything else is a real address or intersection.

## For Didrik: pipe capacities

For both pipe types to be used in Step 1, the capacities need to work with this demand:
- C3 (109 kW) and C4 (50 kW) can be fed by DN-30 service pipes.
- C0, C1 and C2 (182–273 kW) need DN-50.
- Pipes near the producers carry up to about 625 kW together, split between P0 and P1.

As a physics check, capacity is P = ρ · v · A · c_p · ΔT, where ρ is water density, v flow velocity, A the pipe's cross-section area, c_p the specific heat of water and ΔT the supply-return temperature difference. At 1.0–1.5 m/s and ΔT = 40 K, this gives roughly:

| Pipe | Capacity range |
|---|---|
| DN-25 | 80–120 kW |
| DN-30 | 115–175 kW |
| DN-40 | 205–310 kW |
| DN-50 | 320–480 kW |
| DN-60 | 465–700 kW |

These use nominal diameter as the inner diameter, so check them against a manufacturer table. If DN-50 is capped below what one producer would need to send, DHNx is forced to use both producers. That is fine, and worth explaining in the report.

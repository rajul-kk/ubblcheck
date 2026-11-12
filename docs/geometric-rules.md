# Geometric rules

`ubblcheck ifc` measures some quantities from the model's geometry instead of
trusting the values the designer typed in. These rules live in
`src/ubblcheck/georules.py` and run by default; `--no-geometry` turns them off.

## Travel distance (by-law 165)

By-law 165(1) says how travel distance is measured:

> along the centre line of the natural path of travel, starting 0.300 metre
> from the most remote point of occupancy, curving around any corners or
> obstructions with 0.300 metre clearance therefrom and ending at the storey
> exit.

The tool follows those words for each storey:

1. **Cut the plan at 1 m above the storey level.** Walls, columns and curtain
   walls are sectioned at that height. IfcOpenShell subtracts openings first,
   so every door shows as a gap in its wall.
2. **Lay a grid over the walking surface.** The walking surface is the union
   of the storey's spaces. The default cell is 0.2 m (`--cell`).
3. **Block what people cannot walk through.** Cells inside a wall, or within
   0.3 m of one, are blocked; that is the clearance. Door openings stay
   passable across their full width, because a door is not an obstruction.
4. **Find shortest paths from the exits.** A Dijkstra search starts from the
   cells of every door flagged `Pset_DoorCommon.FireExit`, giving each cell
   its walking distance to the nearest storey exit. Moves go in 16
   directions (knight moves included), so paths bend smoothly around corners.
5. **Take each space's worst point.** That is the largest distance among
   cells at least 0.3 m in from the space boundary.

Further rules apply on top of that:

- **Small rooms (by-law 165(3)).** If a room's occupant load is six or fewer
  (Seventh Schedule factors), distance is measured from its door. Separately,
  the distance from the room's remote point to that door must not exceed
  15 m.
- **Open plan (by-law 165(2)).** If the building's `SubUse` ends in
  `open_plan`, or a space has `MY_FireSafety.OpenPlan = TRUE`, the
  straight-line distance to the nearest exit must not exceed two-thirds of the
  travel limit.

**Accuracy.** On an open 40 × 20 m floor the tool gives 41.78 m against
41.82 m exact. With two exits on a 60 × 20 m floor it gives 34.06 m against
33.60 m (+1.4 %).

- Sixteen directions over-estimate a straight run by at most about 2.7 %.
- The grid adds up to about one cell.

A result above the limit but inside that tolerance is reported as a
**warning** ("measure by hand"), not an error. This keeps false flags down.

| Rule | Finding |
|---|---|
| GEO-TRAVEL | Measured walking distance over the Seventh Schedule limit |
| GEO-ROOM | Small room: remote point to room door over 15 m |
| GEO-DIRECT | Open plan: direct distance over two-thirds of the limit |
| GEO-NOROUTE | More than 5 % of a space has no route to any exit (missing door, or a door not cut into its wall) |
| GEO-NOEXIT | An occupied storey with no exit: no fire exit door, and no escape stair coming up to it. A storey with no exit door is measured to its escape stairs, the same exits IFC-EXIT-COUNT counts |
| GEO-EXIT-GEOM | A storey's exits have no plan geometry (warning: travel not measured) |
| GEO-EXIT-SPACING | No two of a storey's exit doors are 5 m apart, edge to edge (by-law 174(1)); skipped where by-law 194 allows one exit |
| GEO-DECLARED | `MaxTravelDistance` in the model is shorter than the measured distance |
| GEO-AREA | Declared floor area differs from the space geometry by more than 5 % |

`--heatmaps` writes one PNG per storey:

- green to amber up to the limit, red beyond it;
- dark grey for walls and the 0.3 m clearance;
- light grey for floor with no route;
- blue for exits.

## Fire appliance access (by-law 140)

A building over 7,000 m³ must abut a road or open space at least 12 m wide
that fire appliances can reach. The share of its perimeter that must abut it
rises with volume, from one-sixth up to an island site.

- **Volume.** The sum of the space volumes, which is how by-law 135(c)
  measures it (inside the enclosing walls). If there are no space solids,
  footprint area times height is used instead.
- **Access.** Any element with `MY_FireSafety.FireApplianceAccess = TRUE`,
  for example an `IfcGeographicElement` road or hardstanding. To keep only
  the parts at least 12 m wide, the outline is shrunk by 6 m and grown back
  by 6 m. If nothing survives, GEO-ACCESS-WIDTH is raised.
- **Abutting.** This is the length of the building's outer perimeter within
  `ABUT_M` (10 m) of that wide access. **The 10 m is an assumption, not
  UBBL text.** Set it to the distance your Bomba office accepts.
- **Hydrants (by-law 225(2)).** At least one `IfcFireSuppressionTerminal`
  FIREHYDRANT must be within 91.5 m of the access (GEO-HYDRANT).

## Purpose group from room use (by-law 134, 138)

Room names, long names and object types are matched against keyword lists in
English and Malay:

| Group | Example words |
|---|---|
| IV | office, pejabat |
| V | shop, kedai, restoran |
| III | bilik tidur, hotel |
| II | ward, taska |
| VII | hall, dewan, surau, classroom |
| VI | factory, kilang |
| VIII | store, gudang, car park |

Ancillary rooms (toilets, lobbies, stairs, plant) are ignored.

- **GEO-GROUP.** The area-weighted main use disagrees with the declared
  purpose group.
- **GEO-MIXED.** At least 10 % of a compartment's named floor area belongs to
  a different group from its main one. By-law 138(c) wants such parts
  separated by compartment walls or floors.
- **GEO-BASEMENT.** A basement storey over 100 m² must have a compartment
  floor over it (by-law 138(d)).

## Not done yet

- **Dead-end distances.** These need the two-direction escape analysis. The
  limit is in the tables, but no geometric check exists.
- **Stairs and multi-storey routes.** Travel ends at the storey exit, as
  by-law 165 defines it; stair widths and staircase capacity
  (by-law 177) are not measured from geometry.
- **Furniture and fixed seating.** These are not treated as obstructions.
- **Real models.** The rules have run on synthetic models and on the
  buildingSMART `Building-Architecture.ifc` sample. That sample declares no
  purpose group and has no doors, so the travel rules did not fire on it.
  Accuracy on real project models is unmeasured; that is D4's job.

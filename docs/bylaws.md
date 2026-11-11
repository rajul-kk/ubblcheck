# What the checker covers, by-law by by-law

UBBL 1984 Parts VII (fire requirements) and VIII (fire alarms, detection, extinguishment
and fire fighting) run from by-law 133 to 253. This table says what the checker does with each
one. `tests/test_bylaws_doc.py` keeps it honest: every by-law the code cites must be listed
here as covered, and every by-law listed as covered must be cited by the code.

| Mark | Meaning |
|---|---|
| **checked** | A rule works out whether the building complies and reports a breach. |
| **data** | The IDS pack asks the model to carry the data or object, but no rule judges it. |
| **shown** | The drawing set must show the item; whether it is right is the reviewer's call. |
| — | Not covered; the last column says why. |

| By-law | Subject | IFC model | Drawing set | Notes, or why not |
|---|---|---|---|---|
| 133 | Interpretations | — | — | Definitions |
| 134 | Purpose groups | checked | — | Declared group used everywhere; room names compared with it (GEO-GROUP, GEO-MIXED). Drawings: a form field that selects the items |
| 135 | Rules of measurement | checked | shown | Height, floor area and volume measured as the by-law says (IFC-HEIGHT, GEO-AREA) |
| 136 | Compartment size limits (Fifth Schedule) | checked | shown | IFC-COMP-AREA, IFC-COMP-VOL, sprinkler doubling |
| 137 | Compartment floors over 30 m | checked | — | IFC-COMP-FLOOR |
| 138 | Other compartment walls and floors | checked | — | (c) mixed purpose groups and (d) floor over a basement only |
| 139 | Separation of fire risk areas | — | — | Possible: needs rooms tagged as risk areas (boiler, transformer, genset) |
| 140 | Fire appliance access | checked | shown | GEO-ACCESS, GEO-ACCESS-WIDTH; drawings: access in yellow (PDF-YELLOW) and the calculation (PG3) |
| 141 | Separating walls | — | — | Needs the adjoining building, which isn't in the model |
| 142 | External walls | — | — | Needs the relevant boundary and unprotected areas (Sixth Schedule) |
| 143 | Beams and columns carrying external walls | — | — | Needs to know which members carry which wall |
| 144 | Cladding on external walls | — | — | Material specification |
| 145 | Reference to the Sixth Schedule | — | — | As 142 |
| 146 | Relevant boundary | — | — | Definition |
| 147 | Construction of separating walls | — | shown | Compartment walls marked on plans (P07) |
| 148 | Special requirements, compartment walls and floors | data | shown | Fire rating asked for on every compartment wall and slab (MY-P08, MY-P09) |
| 149 | Horizontal and vertical barriers in external walls | — | — | Needs facade geometry (spandrels) |
| 150 | Protected shafts | — | — | Possible: shaft spaces enclosed by rated walls |
| 151–152 | Lift shaft ventilation and openings | — | — | Construction detail |
| 153 | Smoke detectors in lift lobbies | data | — | Detector objects asked for (active IDS); placement not judged |
| 154–155 | Lift emergency and fire modes | — | — | Operation, not drawn |
| 156–157 | Shafts as ducts; staircase shafts | — | — | Construction detail |
| 158–159 | Stages | — | — | Places of assembly with stages: not yet |
| 160 | Air-conditioning systems | — | — | M&E detail |
| 161 | Fire-stopping | — | — | Construction detail |
| 162 | Fire doors in compartment and separating walls | checked | shown | IFC-FIREDOOR, IFC-FIREDOOR-WALL; drawings P04 |
| 163 | Half-hour and one-hour doors | data | shown | Rating format (MY-P12); drawings P04 |
| 164 | Door closers | — | shown | Drawings P04. Possible in IFC: Pset_DoorCommon.SelfClosing |
| 165 | Travel distance | checked | checked | Declared (IFC-TRAVEL) and computed on plan (GEO-TRAVEL, GEO-DIRECT, GEO-ROOM); drawings PDF-TRAVEL, PDF-DEADEND |
| 166 | Exits accessible at all times | — | — | Operation |
| 167 | Storey exits | checked | shown | Exit doors (MY-P10); an upper storey with none escapes by an escape stair from the storey below. GEO-NOEXIT; drawings P03 |
| 168 | Staircases | data | shown | Escape stair flag (MY-P13); stair widths not judged |
| 169–171 | Exit routes, unenclosed openings, horizontal exits | data | — | Data asked for; routes not judged |
| 172 | Exit signs | data | shown | Drawings P13 |
| 173 | Exit doors | data | — | Possible: door swing against direction of travel |
| 174 | Arrangement of storey exits | data | — | Possible: exits remote from each other, from plan geometry |
| 175 | Occupant load | checked | shown | Seventh Schedule loads; drawings: calculation table (PG2) |
| 176 | Storey exit width | checked | shown | IFC-EXIT-WIDTH |
| 177 | Number and width of staircases | checked | shown | Two exits per storey (177(d), IFC-EXIT-COUNT); stair widths and (a)-(c), (e)-(f) not judged |
| 178–188 | Places of assembly: exits, seating, gangways, travel | — | — | Not yet: needs seating layout and the assembly class of by-law 179 |
| 189–191 | Enclosure of escape routes, external stairs, adjacent openings | — | — | Construction detail |
| 192–193 | Moving walks, power-operated doors | — | — | Rare; not modelled |
| 194 | Building with single staircase | checked | — | One exit is a warning, not an error, for domestic and office buildings with the topmost floor at most 12 m (IFC-EXIT-SINGLE); the warning lists conditions (a), (b), (i), (iii) and (iv) to confirm |
| 195 | Staircases to roof level over 30 m | — | — | Possible: stair reaching the top storey |
| 196–202 | Smoke and protected lobbies, staircase ventilation, pressurisation | — | shown | Locations marked (P08) |
| 203–212 | Spread of flame, roofs | — | — | Needs surface classes; possible via Pset_*Common.SurfaceSpreadOfFlame |
| 213 | Fire resistance of elements of structure (Ninth Schedule) | checked | shown | IFC-FRP per compartment; drawings P06 |
| 214 | External and separating walls, minimum periods | — | shown | Possible: external walls 30 min, separating walls 60 min |
| 215 | Height and compartments for fire resistance | — | shown | Compartment sizes are used; part-height separation is not |
| 216 | Single-storey buildings; elements in several compartments | checked | shown | (1) exemptions (IFC-FRP-SINGLE); (2) strictest compartment wins |
| 217–224 | Structural members, flats, floors, capacity, tests, deemed-to-satisfy | — | shown | Cited by the fire resistance note item (P06) |
| 225 | Detection and extinguishment (Tenth Schedule) | checked | checked | IFC-TENTH-*, hydrant within 91.5 m (GEO-HYDRANT); drawings PDF-TENTH and the system items |
| 226 | Automatic systems, hazardous occupancy | data | — | |
| 227 | Portable extinguishers | — | shown | P11 |
| 228 | Sprinkler valves | — | shown | A04 |
| 229 | Over 18.3 m: access and fire fighting | checked | shown | IFC-RISER, IFC-FIRELIFT; drawings P14, P17 |
| 230–231 | Dry and wet rising systems | checked | shown | Dry or wet by height (IFC-RISER, IFC-WETRISER); testing is on site |
| 232 | Rising systems during construction | — | — | Construction stage |
| 233–234 | Foam inlets | — | — | Possible: basements and windowless buildings |
| 235 | Fixed installations | — | shown | P09 |
| 236 | Special hazards | — | — | Case by case |
| 237 | Fire alarms | checked | shown | Through the Tenth Schedule (IFC-TENTH-1, -2); drawings P15, AO3 |
| 238 | Command and control centre | — | — | Possible: room presence in tall buildings |
| 239 | Voice communication | — | shown | P16, AO2 |
| 240–241 | Isolation switch; alarm system details | — | — | M&E detail |
| 242 | Fire-fighting access lobbies | — | — | Possible, with 229 |
| 243 | Fire lift | checked | shown | IFC-FIRELIFT; drawings P17 |
| 244–246 | Standards, approval, certification | — | — | Administrative |
| 247 | Water storage | — | shown | A02, AS2 |
| 248 | Markings on wet risers | — | shown | A02 |
| 249–252 | Smoke venting | — | — | Possible: basements and windowless buildings |
| 253 | Emergency power | — | shown | Emergency lights (P12) |

## What the table shows

Means of escape and compartmentation, the core of a passive submission, are checked on the
model. On the drawings they are mostly *shown*; travel distances and ramp gradients written
on the drawings are read and compared with the limits. The gaps that look worth closing next are listed in
`goals.md`. The rest need information a model or drawing set doesn't hold (boundaries,
neighbours, materials) or are about construction, operation and approval.

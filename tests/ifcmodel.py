"""Small synthetic IFC4 office models for tests (no geometry)."""
import ifcopenshell
import ifcopenshell.api.aggregate as agg
import ifcopenshell.api.feature as feat
import ifcopenshell.api.pset as pset
import ifcopenshell.api.root as root
import ifcopenshell.api.spatial as spatial
import ifcopenshell.api.unit as unit


def _ps(m, el, name, props):
    p = pset.add_pset(m, product=el, name=name)
    pset.edit_pset(m, pset=p, properties=props)


def _qto(m, el, name, props):
    q = pset.add_qto(m, product=el, name=name)
    pset.edit_qto(m, qto=q, properties=props)


def office(storeys=2, space_area=400.0, comp_split=True, travel=40.0, exit_width=1.2,
           exits_per_storey=2, door_frp="60", wall_frp="60", sprinklered=False,
           group="IV", height=None, storey_h=4.0, slab_comp=True, tags=True, fire_lift=False,
           breeching=False, roof=False):
    m = ifcopenshell.file(schema="IFC4")
    proj = root.create_entity(m, ifc_class="IfcProject", name="Test")
    unit.assign_unit(m, length={"is_metric": True, "raw": "METRES"})
    site = root.create_entity(m, ifc_class="IfcSite", name="Site")
    bld = root.create_entity(m, ifc_class="IfcBuilding", name="Office")
    agg.assign_object(m, products=[site], relating_object=proj)
    agg.assign_object(m, products=[bld], relating_object=site)
    h = height if height is not None else storeys * storey_h
    props = {"BuildingHeight": m.createIfcLengthMeasure(float(h))}
    if group is not None:
        props["PurposeGroup"] = group
    _ps(m, bld, "MY_FireSafety", props)
    _ps(m, bld, "Pset_BuildingCommon", {"SprinklerProtectionAutomatic": sprinklered})
    for i in range(storeys):
        st = root.create_entity(m, ifc_class="IfcBuildingStorey", name=f"L{i}")
        st.Elevation = i * storey_h
        agg.assign_object(m, products=[st], relating_object=bld)
        items = []
        for j in range(2):
            sp = root.create_entity(m, ifc_class="IfcSpace", name=f"L{i}-S{j}")
            agg.assign_object(m, products=[sp], relating_object=st)
            cid = f"C{i}-{j}" if comp_split else f"C{i}"
            _ps(m, sp, "MY_FireSafety", {"CompartmentId": cid,
                                         "MaxTravelDistance": m.createIfcLengthMeasure(float(travel))})
            _qto(m, sp, "Qto_SpaceBaseQuantities", {"NetFloorArea": space_area, "Height": 3.5})
        wall = root.create_entity(m, ifc_class="IfcWall", name=f"CW{i}")
        _ps(m, wall, "Pset_WallCommon", {"Compartmentation": True, "FireRating": wall_frp})
        items.append(wall)
        for k in range(exits_per_storey):
            d = root.create_entity(m, ifc_class="IfcDoor", name=f"D{i}{k}")
            d.OverallWidth = exit_width
            dp = {"FireExit": True}
            if door_frp is not None:
                dp["FireRating"] = door_frp
            _ps(m, d, "Pset_DoorCommon", dp)
            op = root.create_entity(m, ifc_class="IfcOpeningElement")
            feat.add_feature(m, feature=op, element=wall)
            feat.add_filling(m, opening=op, element=d)
            items.append(d)
        stair = root.create_entity(m, ifc_class="IfcStair", name=f"ST{i}")
        _ps(m, stair, "Pset_StairCommon", {"FireExit": True})
        slab = root.create_entity(m, ifc_class="IfcSlab", name=f"SL{i}", predefined_type="FLOOR")
        _ps(m, slab, "Pset_SlabCommon", {"Compartmentation": slab_comp, "FireRating": "120"})
        items += [stair, slab]
        hr = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal", name=f"HR{i}",
                                predefined_type="HOSEREEL")
        if tags:
            hr.Tag = f"HR{i + 1}"
        items.append(hr)
        if fire_lift:
            lift = root.create_entity(m, ifc_class="IfcTransportElement", name=f"FL{i}",
                                      predefined_type="ELEVATOR")
            _ps(m, lift, "Pset_TransportElementElevator", {"FireFightingLift": True})
            items.append(lift)
        spatial.assign_container(m, products=items, relating_structure=st)
    if roof:
        # a roof level exported as a storey: a roof slab and no spaces
        st = root.create_entity(m, ifc_class="IfcBuildingStorey", name="Roof")
        st.Elevation = storeys * storey_h
        agg.assign_object(m, products=[st], relating_object=bld)
        rf = root.create_entity(m, ifc_class="IfcSlab", name="RF", predefined_type="ROOF")
        spatial.assign_container(m, products=[rf], relating_structure=st)
    hyd = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal", name="H1", predefined_type="FIREHYDRANT")
    hyd.Tag = "H1"
    site_items = [hyd]
    if breeching:
        bi = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal", name="BI1",
                                predefined_type="BREECHINGINLET")
        bi.Tag = "BI1"
        site_items.append(bi)
    spatial.assign_container(m, products=site_items, relating_structure=site)
    return m


def to_millimetres(m):
    """Re-express a metre model the way Revit exports it: lengths in mm, areas in m2, volumes in m3."""
    for u in m.by_type("IfcUnitAssignment"):
        m.remove(u)
    unit.assign_unit(m, length={"is_metric": True, "raw": "MILLIMETERS"},
                     area={"is_metric": True, "raw": "SQUARE_METERS"},
                     volume={"is_metric": True, "raw": "CUBIC_METERS"})
    for st in m.by_type("IfcBuildingStorey"):
        st.Elevation = (st.Elevation or 0.0) * 1000
    for d in m.by_type("IfcDoor"):
        if d.OverallWidth:
            d.OverallWidth *= 1000
    for p in m.by_type("IfcPropertySingleValue"):
        if p.NominalValue is not None and p.NominalValue.is_a("IfcLengthMeasure"):
            p.NominalValue.wrappedValue *= 1000
    for q in m.by_type("IfcQuantityLength"):
        q.LengthValue *= 1000
    return m


def add_column(m, storey_index=0, rating=None, name="C1"):
    st = sorted(m.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0.0)[storey_index]
    c = root.create_entity(m, ifc_class="IfcColumn", name=name)
    if rating is not None:
        _ps(m, c, "Pset_ColumnCommon", {"FireRating": rating})
    spatial.assign_container(m, products=[c], relating_structure=st)
    return c

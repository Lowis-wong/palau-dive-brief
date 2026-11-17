"""Build the Project 10 QGIS project on Lowis's Mac (run inside QGIS: Python console, or via QGIS MCP execute_code).

Two steps so that no single call blocks QGIS for long:
    exec(open(PATH_TO_THIS_FILE).read())
    start_download()      # background thread; returns at once
    download_status()     # poll until done == total
    build_project()       # creates and saves ~/Backup/Palau_QGIS/Palau_Bathymetry_P10.qgz

Data come from the public analysis-data branch (outputs of pilot.py, sdb.py, derive.py).
Kept under analysis/project10/ (not scripts/) so that editing it does not trigger the analysis workflow.
"""
import os
import threading
import urllib.request

BASE = "https://raw.githubusercontent.com/Lowis-wong/palau-dive-brief/analysis-data/project10/"
ROOT = os.path.expanduser("~/Backup/Palau_QGIS")
DATA = os.path.join(ROOT, "project10")
PROJECT = os.path.join(ROOT, "Palau_Bathymetry_P10.qgz")
FILES = [
    "products/depth_display.tif", "products/depth_quality.tif", "products/uncertainty_m.tif",
    "products/hillshade.tif", "products/rgb_display.tif", "products/contours.gpkg",
    "products/atl24_cells.gpkg", "products/products.json",
    "sdb/depth_mllw_median.tif", "sdb/depth_iqr.tif", "sdb/optically_deep_frac.tif", "sdb/depth_count.tif",
    "boats/blue-corner/hotspots.csv", "boats/german-channel/hotspots.csv",
]
SITES = [("Blue Corner", 7.135, 134.220), ("Siaes Corner", 7.312, 134.225),
         ("Ulong Channel", 7.2833, 134.2456), ("German Channel", 7.1210, 134.2720)]
_state = {"done": 0, "total": len(FILES), "errors": [], "running": False}


def _download():
    _state.update(done=0, errors=[], running=True)
    for f in FILES:
        p = os.path.join(DATA, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            tmp = p + ".part"
            urllib.request.urlretrieve(BASE + f, tmp)
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001
            _state["errors"].append(f"{f}: {e}")
        _state["done"] += 1
    _state["running"] = False


def start_download():
    if _state["running"]:
        return "already running"
    threading.Thread(target=_download, daemon=True).start()
    return "started"


def download_status():
    return dict(_state)


def build_project():
    from qgis.core import (QgsColorRampShader, QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsGeometry,
                           QgsGraduatedSymbolRenderer, QgsLayerTreeGroup, QgsMarkerSymbol, QgsMultiBandColorRenderer,
                           QgsPalLayerSettings, QgsPointXY, QgsProject, QgsRasterLayer, QgsRasterShader,
                           QgsRendererRange, QgsSingleBandGrayRenderer, QgsSingleBandPseudoColorRenderer,
                           QgsSymbol, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
                           QgsPalettedRasterRenderer, QgsLineSymbol)
    from qgis.PyQt.QtCore import QVariant
    from qgis.PyQt.QtGui import QColor, QPainter

    prj = QgsProject.instance()
    prj.clear()
    prj.setCrs(QgsCoordinateReferenceSystem("EPSG:32653"))
    prj.setTitle("Palau Bathymetry - Project 10 (satellite-derived depth, MLLW)")
    root = prj.layerTreeRoot()

    def group(name):
        g = root.addGroup(name)
        return g

    def add(layer, grp, visible=True):
        if not layer.isValid():
            raise RuntimeError(f"invalid layer {layer.name()}: {layer.source()}")
        prj.addMapLayer(layer, False)
        node = grp.addLayer(layer)
        node.setItemVisibilityChecked(visible)
        return layer

    def d(f):
        return os.path.join(DATA, f)

    def pseudo(layer, stops, opacity=1.0, labels=None):
        sh = QgsColorRampShader()
        sh.setColorRampType(QgsColorRampShader.Interpolated)
        items = []
        for i, (v, c) in enumerate(stops):
            lab = labels[i] if labels else f"{v:g}"
            items.append(QgsColorRampShader.ColorRampItem(v, QColor(c), lab))
        sh.setColorRampItemList(items)
        sh.setMinimumValue(stops[0][0]); sh.setMaximumValue(stops[-1][0])
        rs = QgsRasterShader(); rs.setRasterShaderFunction(sh)
        r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, rs)
        r.setClassificationMin(stops[0][0]); r.setClassificationMax(stops[-1][0])
        layer.setRenderer(r)
        layer.renderer().setOpacity(opacity)

    # groups (top of legend first)
    g_moor = group("浮球候选 Mooring candidates")
    g_ref = group("参考 Reference")
    g_bath = group("水深 Bathymetry (m below MLLW)")
    g_check = group("检查 QA layers")
    g_icesat = group("ICESat-2")
    g_s2 = group("Sentinel-2")
    g_base = group("底图 Basemaps")

    # basemaps
    esri = QgsRasterLayer("type=xyz&zmin=0&zmax=19&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
                          "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D", "Esri World Imagery", "wms")
    add(esri, g_base, True)
    osm = QgsRasterLayer("type=xyz&zmin=0&zmax=19&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png",
                         "OpenStreetMap", "wms")
    add(osm, g_base, False)

    # Sentinel-2 RGB
    rgb = QgsRasterLayer(d("products/rgb_display.tif"), "S2 median RGB 2017-2026 (glint removed)")
    rr = QgsMultiBandColorRenderer(rgb.dataProvider(), 1, 2, 3)
    rgb.setRenderer(rr)
    add(rgb, g_s2, False)

    # bathymetry
    con = QgsVectorLayer(d("products/contours.gpkg") + "|layername=contours", "Contours (m)", "ogr")
    sym = QgsLineSymbol.createSimple({"color": "30,55,90,200", "width": "0.18"})
    from qgis.core import QgsRuleBasedRenderer
    rule_r = QgsRuleBasedRenderer(QgsLineSymbol.createSimple({"color": "30,55,90,200", "width": "0.18"}))
    top = rule_r.rootRule()
    for ch in list(top.children()):
        top.removeChild(ch)
    major = QgsRuleBasedRenderer.Rule(QgsLineSymbol.createSimple({"color": "20,40,70,230", "width": "0.45"}),
                                      filterExp='"major"', label="5 / 10 / 15 m")
    minor = QgsRuleBasedRenderer.Rule(sym, filterExp='NOT "major"', label="1-4, 6, 8, 12 m")
    top.appendChild(major); top.appendChild(minor)
    con.setRenderer(rule_r)
    pal = QgsPalLayerSettings()
    pal.fieldName = "depth_m || ' m'"
    pal.isExpression = True
    pal.placement = QgsPalLayerSettings.Curved
    tf = QgsTextFormat(); tf.setSize(7); tf.setColor(QColor(20, 40, 70))
    pal.setFormat(tf)
    con.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    con.setLabelsEnabled(True)
    add(con, g_bath, True)

    q = QgsRasterLayer(d("products/depth_quality.tif"), "Fair quality (washed out)")
    classes = [QgsPalettedRasterRenderer.Class(1, QColor(0, 0, 0, 0), "good"),
               QgsPalettedRasterRenderer.Class(2, QColor(255, 255, 255, 110), "fair: less certain")]
    q.setRenderer(QgsPalettedRasterRenderer(q.dataProvider(), 1, classes))
    add(q, g_bath, True)

    hs = QgsRasterLayer(d("products/hillshade.tif"), "Hillshade")
    gr = QgsSingleBandGrayRenderer(hs.dataProvider(), 1)
    hs.setRenderer(gr)
    hs.setBlendMode(QPainter.CompositionMode_Multiply)
    hs.renderer().setOpacity(0.55)
    add(hs, g_bath, True)

    dep = QgsRasterLayer(d("products/depth_display.tif"), "Depth (SDB v3, 10 m)")
    pseudo(dep, [(0, "#e3f6ea"), (2, "#b5e5d6"), (5, "#6fcbcf"), (10, "#2f93c4"), (15, "#245aa3"), (18, "#1b2f6b")],
           opacity=0.85, labels=["0", "2", "5", "10", "15", "18 m+"])
    add(dep, g_bath, True)

    # QA
    unc = QgsRasterLayer(d("products/uncertainty_m.tif"), "Uncertainty 1-sigma (m)")
    pseudo(unc, [(0, "#2c7bb6"), (1, "#abd9e9"), (2, "#ffffbf"), (3, "#fdae61"), (5, "#d7191c")])
    add(unc, g_check, False)
    for f, name, stops in [
        ("sdb/depth_mllw_median.tif", "Depth raw median (unclipped)",
         [(0, "#e3f6ea"), (5, "#6fcbcf"), (10, "#2f93c4"), (20, "#1b2f6b"), (30, "#000000")]),
        ("sdb/optically_deep_frac.tif", "Optically deep fraction", [(0, "#ffffff"), (0.5, "#888888"), (1, "#000000")]),
        ("sdb/depth_iqr.tif", "Between-scene IQR (m)", [(0, "#ffffff"), (3, "#fdae61"), (8, "#d7191c")]),
        ("sdb/depth_count.tif", "Scenes used", [(0, "#ffffff"), (100, "#9ecae1"), (280, "#08519c")]),
    ]:
        lyr = QgsRasterLayer(d(f), name)
        pseudo(lyr, stops)
        add(lyr, g_check, False)

    # ICESat-2
    at = QgsVectorLayer(d("products/atl24_cells.gpkg") + "|layername=atl24_cells", "ATL24 seafloor cells (10 m)", "ogr")
    ranges = []
    for lo, hi, c in [(0, 2, "#ffffb2"), (2, 5, "#a1dab4"), (5, 10, "#41b6c4"), (10, 15, "#2c7fb8"), (15, 30, "#253494")]:
        s = QgsMarkerSymbol.createSimple({"name": "circle", "color": c, "size": "0.8", "outline_style": "no"})
        ranges.append(QgsRendererRange(lo, hi, s, f"{lo}-{hi} m"))
    at.setRenderer(QgsGraduatedSymbolRenderer("depth", ranges))
    add(at, g_icesat, False)

    # reference sites
    sites = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string", "Dive sites", "memory")
    pr = sites.dataProvider()
    for n, lat, lon in SITES:
        ft = QgsFeature(); ft.setAttributes([n]); ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        pr.addFeature(ft)
    sites.updateExtents()
    from qgis.core import QgsVectorFileWriter
    out = d("sites.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions(); opts.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV3(sites, out, prj.transformContext(), opts)
    sites = QgsVectorLayer(out, "Dive sites", "ogr")
    sites.setRenderer(sites.renderer().clone())
    sites.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#e31a1c", "size": "3"}))
    pl = QgsPalLayerSettings(); pl.fieldName = "name"
    tf2 = QgsTextFormat(); tf2.setSize(9); tf2.setColor(QColor("#e31a1c")); pl.setFormat(tf2)
    sites.setLabeling(QgsVectorLayerSimpleLabeling(pl)); sites.setLabelsEnabled(True)
    add(sites, g_ref, True)

    # mooring candidates (pilot: Blue Corner, German Channel)
    for key, name in [("blue-corner", "Blue Corner"), ("german-channel", "German Channel")]:
        uri = ("file://" + d(f"boats/{key}/hotspots.csv") +
               "?delimiter=,&xField=lon&yField=lat&crs=EPSG:4326")
        lyr = QgsVectorLayer(uri, f"Boat hotspots (pilot) - {name}", "delimitedtext")
        lyr.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "star", "color": "#ff7f00", "size": "3.2",
                                                                "outline_color": "#000000", "outline_width": "0.2"}))
        pl = QgsPalLayerSettings(); pl.fieldName = "n_scenes"
        tf3 = QgsTextFormat(); tf3.setSize(7); pl.setFormat(tf3)
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(pl)); lyr.setLabelsEnabled(True)
        add(lyr, g_moor, True)

    prj.write(PROJECT)
    return {"saved": PROJECT, "layers": len(prj.mapLayers())}

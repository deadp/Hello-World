"""Download inputs for the cycle network gap analysis (cycle_network.py, cycle_model.py).

  data/raw/cycling/osm_ways.json        OpenStreetMap highways and paths in the Wellington City
                                        bbox (Overpass API), with tags and geometry
  data/raw/cycling/osm_education.json   schools, colleges and universities (OSM)
  data/raw/cycling/dem_5m_{0,1}.tif     WCC 1 m LiDAR DEM (2020) resampled to 5 m, two tiles
  data/raw/networks/wcc_bike_network.parquet  WCC Strategic Bike Network 2022 (current facility,
                                        network class, stage)
  data/raw/cycling/census_work_od.csv   2023 Census main means of travel to work, SA2 -> SA2
  data/raw/cycling/census_edu_od.csv    2023 Census main means of travel to education, SA2 -> SA2
  data/raw/cycling/osm_nodes.json       OSM traffic signals, crossings and cycle barriers
  data/raw/cycling/speed_zones.geojson  NZTA National Speed Limit Register zones in force today
                                        (Wellington City and state highways; polygons, NZTM; CC BY 4.0)
  data/raw/cycling/sh_sites.geojson     NZTA state highway traffic monitoring sites with AADT
  data/raw/cycling/sensors/             WCC transport sensors (VivaCity): countline metadata,
                                        hourly cyclist counts from Nov 2023 and daily sensor
                                        availability (public S3 bucket)
  data/raw/cycling/schools.geojson      Ministry of Education schools directory (type, roll, SA2),
                                        via Eagle Technology's ArcGIS copy (data.govt.nz blocks
                                        scripted downloads)
  data/raw/cycling/osm_destinations.json  OSM shops, leisure and community destinations

Run with --extras to fetch only the NZTA, sensor, school and destination layers.
"""

import sys
import time
from pathlib import Path

import requests

from fetch_networks import fetch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "cycling"
BBOX = "(-41.37,174.61,-41.14,174.90)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
HEADERS = {"User-Agent": "nz-lvt-research/1.0 (cycling network analysis)", "Accept": "application/json"}
WCC = "https://gis.wcc.govt.nz/arcgis/rest/services"
DEM = f"{WCC}/Elevation/DigitalElevation1Metre_2020/ImageServer/exportImage"
DEM_EXTENT = (1742080, 5418960, 1756480, 5443440)
DEM_RES = 5
NZTA = "https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
NZTM_BOX = "1735000,5415000,1765000,5445000"
SCHOOLS = ("https://services.arcgis.com/XTtANUDT8Va4DLwI/arcgis/rest/services/Schools_Directory_New_Zealand/"
           "FeatureServer/0")
SENSORS = "https://gis-snowflake-opendata-public-wcc-arcgis-prod.s3.ap-southeast-2.amazonaws.com/transport_sensors"
# Stats NZ ArcGIS Hub items (CSV).
CENSUS = {"census_work_od.csv": "fedc12523d4f4da08f094cf13bb21807",
          "census_edu_od.csv": "1cc7c8d8e99e4e428c3359172f49effc"}


def overpass(query, path):
    for url in OVERPASS:
        for attempt in range(2):
            try:
                r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=300)
                if r.ok and r.text.lstrip().startswith("{"):
                    path.write_text(r.text)
                    print(f"{path.name}: {len(r.text) / 1e6:.1f} MB from {url}")
                    return
            except requests.RequestException:
                pass
            time.sleep(5)
    raise RuntimeError("all Overpass mirrors failed")


def dem():
    xmin, ymin, xmax, ymax = DEM_EXTENT
    ymid = (ymin + ymax) / 2
    for i, (y0, y1) in enumerate([(ymin, ymid), (ymid, ymax)]):
        w, h = int((xmax - xmin) / DEM_RES), int((y1 - y0) / DEM_RES)
        r = requests.get(DEM, params=dict(bbox=f"{xmin},{y0},{xmax},{y1}", bboxSR=2193, imageSR=2193,
                                          size=f"{w},{h}", format="tiff", pixelType="F32",
                                          interpolation="RSP_BilinearInterpolation", f="image"), timeout=600)
        r.raise_for_status()
        (OUT / f"dem_5m_{i}.tif").write_bytes(r.content)
        print(f"dem_5m_{i}.tif: {w}x{h}, {len(r.content) / 1e6:.1f} MB")


def arcgis_geojson(url, where, fields, path, page=1000, box=NZTM_BOX, box_sr=2193):
    feats, offset = [], 0
    while True:
        r = requests.get(f"{url}/query", params=dict(
            where=where, outFields=fields, geometry=box, geometryType="esriGeometryEnvelope", inSR=box_sr,
            spatialRel="esriSpatialRelIntersects", outSR=2193, resultOffset=offset, resultRecordCount=page,
            f="geojson"), timeout=300)
        r.raise_for_status()
        got = r.json().get("features", [])
        feats += got
        if len(got) < page:
            break
        offset += page
    import json
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"{path.name}: {len(feats):,} features")


def extras():
    OUT.mkdir(parents=True, exist_ok=True)
    overpass('[out:json][timeout:120];(node["highway"~"^(traffic_signals|crossing)$"]' + BBOX +
             ';node["crossing"]' + BBOX + ';node["barrier"="cycle_barrier"]' + BBOX + ';);out body;',
             OUT / "osm_nodes.json")
    arcgis_geojson(f"{NZTA}/SpeedLimitZoneFull__View/FeatureServer/0",
                   "rcaZoneReferenceName IN ('Wellington City','State Highways') AND whenEffective<=CURRENT_TIMESTAMP"
                   " AND (whenIneffective IS NULL OR whenIneffective>CURRENT_TIMESTAMP)",
                   "speedLimitZoneValue,speedCategoryName,speedLimitZoneName,speedLimitZoneReasonName,whenEffective",
                   OUT / "speed_zones.geojson")
    arcgis_geojson(f"{NZTA}/Assets_SHTrafficMonitoringSites/FeatureServer/0", "1=1", "*", OUT / "sh_sites.geojson")
    arcgis_geojson(SCHOOLS, "1=1", "School_Id,Org_Name,Org_Type,Total,Statistical_Area_2_Code,Status,Roll_Date",
                   OUT / "schools.geojson", box="174.61,-41.37,174.90,-41.14", box_sr=4326)
    overpass('[out:json][timeout:180];(nwr["shop"]' + BBOX + ';nwr["leisure"~"^(park|sports_centre|pitch|'
             'swimming_pool|playground|stadium|garden|marina|fitness_centre)$"]' + BBOX + ';nwr["natural"="beach"]' + BBOX +
             ';nwr["tourism"~"^(attraction|museum|gallery|viewpoint|zoo)$"]' + BBOX + ';nwr["amenity"~"^(library|cinema|'
             'theatre|cafe|restaurant|pub|bar|community_centre|marketplace|place_of_worship)$"]' + BBOX + ';);out tags center;',
             OUT / "osm_destinations.json")
    sens = OUT / "sensors"
    sens.mkdir(exist_ok=True)
    for name, key in [("meta.csv", "countline_meta_info/csv/countline_meta_info.csv"),
                      ("cyclist.csv", "countline_mobility/csv/countline_mobility_cyclist.csv"),
                      ("availability.csv", "viewpoint_availability_daily/csv/viewpoint_availability_daily.csv")]:
        r = requests.get(f"{SENSORS}/{key}", timeout=900)
        r.raise_for_status()
        (sens / name).write_bytes(r.content)
        print(f"sensors/{name}: {len(r.content) / 1e6:.1f} MB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if "--extras" in sys.argv:
        extras()
        return
    overpass(f'[out:json][timeout:170];way["highway"]{BBOX};out tags geom;', OUT / "osm_ways.json")
    overpass(f'[out:json][timeout:120];nwr["amenity"~"^(school|college|university)$"]{BBOX};out tags center;',
             OUT / "osm_education.json")
    dem()
    # Written to data/raw/networks/wcc_bike_network.parquet (NZTM).
    fetch("wcc_bike_network", f"{WCC}/Transportation/Cycling/MapServer/3", "1=1",
          "Current_facility,Street_name,Street_section,Network_class,Network_stage")
    for name, item in CENSUS.items():
        r = requests.get(f"https://www.arcgis.com/sharing/rest/content/items/{item}/data", timeout=300)
        r.raise_for_status()
        (OUT / name).write_bytes(r.content)
        print(f"{name}: {len(r.content) / 1e6:.1f} MB")
    extras()


if __name__ == "__main__":
    main()

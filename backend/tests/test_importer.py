"""The importer has to survive real departmental spreadsheets.

Each format below is deliberately different in the way departments actually
differ: our own column names, an abbreviated engineering export, a verbose
municipal sheet with title-case headers and its own status vocabulary, and a
semicolon-delimited file with the coordinate columns named after axes.
"""
from app.services import importer

OURS = (
    "camera_id,name,department,district,location_name,latitude,longitude,"
    "stream_url,status,ai_enabled\n"
    "GJ-001,Paldi North,TRAFFIC,Ahmedabad,Paldi Circle,23.0107,72.5619,"
    "rtsp://10.0.0.5:554/s1,ONLINE,yes\n"
)

ABBREVIATED = (
    "cam_no,dept,dist,place,lat,lng,rtsp_url,make,live,anpr\n"
    "JN-14,TRAFFIC,Junagadh,Majevadi Gate,21.5222,70.4579,"
    "rtsp://10.2.0.9:554/ch1,Hikvision,yes,1\n"
)

VERBOSE = (
    "Device Code;Camera Name;Owning Department;Zone;Installed At;"
    "GPS Lat;GPS Lon;Feed URL;Operational;ANPR Capable\n"
    "AMC/CCTV/0042;Law Garden Gate 2;MUNICIPAL;Ahmedabad;Law Garden;"
    "23.0225;72.5610;rtsp://10.9.9.9:554/lg2;Working;Yes\n"
)


def _fields(preview):
    return {m["source"]: m["field"] for m in preview["mappings"]}


def test_our_own_headers_map_exactly():
    preview = importer.preview(OURS)
    assert preview["summary"]["unmapped"] == 0
    assert all(m["method"] == "exact" for m in preview["mappings"])
    assert preview["summary"]["importable"] == 1


def test_abbreviated_headers_map_by_synonym():
    preview = importer.preview(ABBREVIATED)
    mapped = _fields(preview)
    assert mapped["cam_no"] == "camera_id"
    assert mapped["lat"] == "latitude"
    assert mapped["lng"] == "longitude"
    assert mapped["rtsp_url"] == "stream_url"
    assert mapped["dept"] == "department"
    # "live" is that sheet's word for up/down, not for plate-reading capability.
    # Ownership and capability are separate questions and must not collapse.
    assert mapped["live"] == "status"
    assert mapped["anpr"] == "ai_enabled"
    values = preview["rows"][0]["values"]
    assert values["latitude"] == 21.5222
    assert values["status"] == "ONLINE"
    assert values["ai_enabled"] is True


def test_verbose_semicolon_sheet_with_local_vocabulary():
    preview = importer.preview(VERBOSE)
    mapped = _fields(preview)
    assert mapped["Device Code"] == "camera_id"
    assert mapped["GPS Lat"] == "latitude"
    assert mapped["GPS Lon"] == "longitude"
    assert mapped["ANPR Capable"] == "ai_enabled"
    values = preview["rows"][0]["values"]
    # "Working" is that department's word for ONLINE, not ours.
    assert values["status"] == "ONLINE"
    assert values["camera_id"] == "AMC/CCTV/0042"


def test_latitude_and_longitude_are_never_swapped():
    """The mapping failure that would silently relocate an entire estate."""
    for text in (OURS, ABBREVIATED, VERBOSE):
        mapped = _fields(importer.preview(text))
        by_field = {v: k for k, v in mapped.items() if v}
        lat_header = by_field["latitude"].lower()
        lon_header = by_field["longitude"].lower()
        assert "lat" in lat_header or "y" in lat_header
        assert "lon" in lon_header or "lng" in lon_header or "x" in lon_header


def test_a_bad_row_never_blocks_a_good_one():
    text = (
        "camera_id,latitude,longitude\n"
        ",23.0,72.5\n"                      # no id -> rejected
        "GJ-002,not-a-number,72.5\n"        # bad lat -> kept, position dropped
        "GJ-003,23.1,72.6\n"                # clean
    )
    preview = importer.preview(text)
    rows = preview["rows"]
    assert rows[0]["ok"] is False
    assert rows[2]["ok"] is True
    # A camera with half a coordinate pair is registered without a position
    # rather than placed at a made-up one.
    assert "latitude" not in rows[1]["values"]
    assert "longitude" not in rows[1]["values"]


def test_unrecognised_columns_are_reported_not_guessed():
    preview = importer.preview("camera_id,warranty_expiry,contractor\nGJ-9,2027-01-01,ACME\n")
    mapped = _fields(preview)
    assert mapped["warranty_expiry"] is None
    assert mapped["contractor"] is None
    assert preview["summary"]["unmapped"] == 2


def test_template_round_trips_through_its_own_importer():
    preview = importer.preview(importer.template_csv())
    assert preview["summary"]["unmapped"] == 0
    assert preview["summary"]["importable"] == 1

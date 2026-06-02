"""FCP 7 XML (xmeml v5) export — structure + drop-frame ntsc flag."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from laura.interchange.fcp7_xml import timeline_to_fcp7_xml
from laura.interchange.timeline import Clip, Timeline


def test_fcp7_xml_structure() -> None:
    tl = Timeline(
        name="Cut", rate_num=30, rate_den=1,
        clips=[
            Clip("A.mov", 0, 30, 0, 30, source_url="C:/m/a.mov"),
            Clip("B.mov", 100, 130, 30, 60),
        ],
    )
    xml = timeline_to_fcp7_xml(tl)
    assert xml.startswith("<?xml")

    root = ET.fromstring(xml)
    assert root.tag == "xmeml" and root.attrib["version"] == "5"
    assert root.findtext("./sequence/rate/timebase") == "30"
    assert root.findtext("./sequence/rate/ntsc") == "FALSE"

    items = root.findall(".//clipitem")
    assert len(items) == 2
    b = items[1]
    assert b.findtext("in") == "100" and b.findtext("out") == "130"
    assert b.findtext("start") == "30" and b.findtext("end") == "60"
    assert "file://localhost/C:/m/a.mov" in xml


def test_fcp7_xml_drop_frame_is_ntsc() -> None:
    tl = Timeline(name="DF", rate_num=30000, rate_den=1001, drop_frame=True,
                  clips=[Clip("A", 0, 1800, 0, 1800)])
    xml = timeline_to_fcp7_xml(tl)
    assert "<timebase>30</timebase>" in xml
    assert "<ntsc>TRUE</ntsc>" in xml

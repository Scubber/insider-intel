"""Tests for slim ITM index refresh helpers."""

from __future__ import annotations

from shared.itm.refresh import slim_matrix


def test_slim_matrix_extracts_sections_and_subsections() -> None:
    payload = {
        "itm_version": "9.9.9",
        "mitre_version": "1.0",
        "articles": [
            {
                "id": "AR4",
                "title": "Infringement",
                "theme": "infringement",
                "sections": [
                    {
                        "id": "IF002",
                        "title": "Exfiltration via Physical Medium",
                        "description_text": "Physical medium exfil.",
                        "detections": [
                            {
                                "id": "DT021",
                                "title": "USBSTOR Registry Key",
                                "description_text": "USBSTOR details.",
                            }
                        ],
                        "preventions": [
                            {
                                "id": "PV037",
                                "title": "Restrict Removable Disk Mounting, Group Policy",
                            }
                        ],
                        "subsections": [
                            {
                                "id": "IF002.001",
                                "title": "Exfiltration via USB",
                                "description_text": "USB stick.",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    slim = slim_matrix(payload, source_url="file://test")
    assert slim["itm_version"] == "9.9.9"
    assert slim["articles"] == [
        {"id": "AR4", "title": "Infringement", "theme": "infringement"}
    ]
    ids = {t["id"] for t in slim["techniques"]}
    assert ids == {"IF002", "IF002.001"}
    parent = next(t for t in slim["techniques"] if t["id"] == "IF002")
    assert "exfiltration via physical medium" in parent["aliases"]
    assert parent["detections"] == [{"id": "DT021", "title": "USBSTOR Registry Key"}]
    assert parent["preventions"] == [
        {"id": "PV037", "title": "Restrict Removable Disk Mounting, Group Policy"}
    ]
    child = next(t for t in slim["techniques"] if t["id"] == "IF002.001")
    assert child["parent_id"] == "IF002"
    assert child["detections"] == []
    assert child["preventions"] == []

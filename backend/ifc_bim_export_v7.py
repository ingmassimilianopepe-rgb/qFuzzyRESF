"""IFC2X3 compatibility exporter for Fuzzy-RESF-BIM v7.

Geometry is exported with the validated v6.5 writer. v7 additionally attaches transparent
object-knowledge metadata to the IFC project and detected doors/windows. This is not a
claim of ISO 16739-1:2024 schema conformance: the portable release intentionally keeps
IFC2X3 exchange compatibility, while recording the newer information standards as
reference frameworks for object/data dictionaries.
"""
from __future__ import annotations

from pathlib import Path
import re
import uuid

from ifc_bim_export_v6_5 import write_bim_ifc as _write_v65

_IFC64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _guid():
    n = uuid.uuid4().int
    chars = []
    for _ in range(22):
        chars.append(_IFC64[n & 63]); n >>= 6
    return "".join(reversed(chars))


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def _inject_object_knowledge(path: Path, model) -> None:
    text = path.read_text(encoding="utf-8", errors="strict")
    ids = [int(x) for x in re.findall(r"#(\d+)=", text)]
    eid = max(ids, default=0) + 1
    injected = []

    def add(expr):
        nonlocal eid
        cur = eid; eid += 1
        injected.append(f"#{cur}={expr};")
        return cur

    owner_m = re.search(r"#(\d+)=IFCOWNERHISTORY\(", text, re.I)
    project_m = re.search(r"#(\d+)=IFCPROJECT\(", text, re.I)
    if not owner_m or not project_m:
        raise RuntimeError("Cannot inject v7 metadata: IFC owner/project not found")
    owner = int(owner_m.group(1)); project = int(project_m.group(1))

    props = [
        add("IFCPROPERTYSINGLEVALUE('MethodVersion',$,IFCLABEL('Fuzzy-RESF-BIM 7.0'),$)"),
        add("IFCPROPERTYSINGLEVALUE('GeometryPolicy',$,IFCLABEL('Standards are soft priors only; scan evidence remains primary'),$)"),
        add("IFCPROPERTYSINGLEVALUE('InformationFramework',$,IFCLABEL('ISO 16739-1:2024; ISO 23387:2025; ISO 23386:2020; ISO 12006-2:2015; ISO 12006-3:2022; ISO 16354:2013'),$)"),
        add("IFCPROPERTYSINGLEVALUE('ExchangeSchemaNote',$,IFCLABEL('Portable compatibility file encoded as IFC2X3; no claim of ISO 16739-1:2024 schema conformance'),$)"),
    ]
    ps = add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_v7_Method',$,({','.join('#'+str(x) for x in props)}))")
    add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{project}),#{ps})")

    for o in getattr(model, "openings", []):
        kind = "DOOR" if str(o.kind).lower() == "door" else "WINDOW"
        label = "Door" if kind == "DOOR" else "Window"
        m = re.search(rf"#(\d+)=IFC{kind}\([^;]*?'{label}_{int(o.id)}'", text, re.I)
        if not m:
            continue
        obj = int(m.group(1))
        type_id = getattr(o, "type_id", "UNIQUE_SCAN_INSTANCE")
        type_score = float(getattr(o, "type_prior_score", 0.0))
        std_score = float(getattr(o, "standards_prior_score", 0.0))
        status = getattr(o, "standards_status", "not_assessed")
        refs = "D.M. 236/1989; UNI EN 14351-1:2016" if (kind == "DOOR" and "external" in str(type_id).lower()) else (
            "D.M. 236/1989; UNI EN 14351-2:2018" if kind == "DOOR" else "UNI EN 14351-1/2 product context")
        p = [
            add(f"IFCPROPERTYSINGLEVALUE('ScanDerivedTypeId',$,IFCLABEL({_q(type_id)}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('TypePriorScore',$,IFCREAL({type_score:.6f}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('StandardsPriorScore',$,IFCREAL({std_score:.6f}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('StandardsStatus',$,IFCLABEL({_q(status)}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('StandardsContext',$,IFCLABEL({_q(refs)}),$)"),
            add("IFCPROPERTYSINGLEVALUE('ComplianceDisclaimer',$,IFCLABEL('Scan inference only; not a certification of regulatory or product-standard compliance'),$)"),
        ]
        pset = add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_ObjectKnowledge',$,({','.join('#'+str(x) for x in p)}))")
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{pset})")

    marker = "\nENDSEC;\nEND-ISO-10303-21;"
    if marker not in text:
        raise RuntimeError("Cannot inject v7 metadata: IFC DATA terminator not found")
    text = text.replace(marker, "\n" + "\n".join(injected) + marker, 1)
    path.write_text(text, encoding="utf-8")


def write_bim_ifc(model, path: str | Path, project_name: str = "Fuzzy-RESF-BIM v7.0") -> None:
    path = Path(path)
    _write_v65(model, path, project_name=project_name)
    _inject_object_knowledge(path, model)

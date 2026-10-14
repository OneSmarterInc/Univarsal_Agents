import hashlib
import re
from datetime import UTC, datetime

from .errors import ServiceError
from .schemas import field_spec


def metadata(status, evidence=None, origin=None, reason=None):
    return {
        "status": status,
        "confidence": None,
        "evidence_ids": evidence or [],
        "origin": origin,
        "reason_code": reason,
    }


def chunks(text, max_bytes=2200, overlap=180):
    start = 0
    while start < len(text):
        end = min(len(text), start + max_bytes)
        while len(text[start:end].encode()) > max_bytes:
            end -= 1
        yield start, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def dates_sane(start, end, today=None):
    year = (today or datetime.now(UTC).date()).year
    values = []
    for value in (start, end):
        if value is None or value.lower() in {"present", "current", "ongoing", "now"}:
            values.append(None)
            continue
        if not re.fullmatch(r"(?:(?:[A-Za-z]{3,9})\s+)?(?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2]))?", value):
            return False
        match = re.search(r"(?:19|20)\d{2}", value)
        found = int(match.group())
        if found > year + 1:
            return False
        values.append(found)
    return not (all(v is not None for v in values) and values[0] > values[1])


def extract_blocks(kind, blocks, model, check=lambda: None, requested=None, initial_warnings=None):
    scalars, lists, records = field_spec(kind)
    data = {**{k: None for k in sorted(scalars)}, **{k: [] for k in sorted(lists | records.keys())}}
    fields, evidence, errors = {}, {}, []
    warnings = list(initial_warnings or [])
    conflicts = set()
    examined = 0
    sent = 0

    def accept(claim, block):
        if (
            claim.block_id != block["id"]
            or claim.quote not in block["text"]
            or claim.value not in claim.quote
        ):
            warnings.append({"code": "unsupported_claim_discarded"})
            return None
        eid = "ev_" + hashlib.sha256((block["id"] + claim.quote).encode()).hexdigest()[:24]
        evidence[eid] = {
            "id": eid,
            "input_id": block["input_id"],
            "source_kind": block["origin"],
            "quote": claim.quote,
            "locator": block["locator"],
            "observed_at": block.get("observed_at", datetime.now(UTC).isoformat()),
            "content_sha256": hashlib.sha256(block["text"].encode()).hexdigest(),
        }
        return eid

    for block in blocks:
        check()
        try:
            result = model.extract(kind, block, check)
        except ServiceError as exc:
            if exc.code in {"job_cancelled", "job_deadline_exceeded"}:
                raise
            errors.append(exc.payload())
            # Runtime failure stops further inference, avoiding repeated calls on a hung runtime.
            if exc.status == 503:
                break
            continue
        examined += 1
        sent += len(block["text"])
        for claim in result.scalars:
            if claim.field not in scalars | lists:
                warnings.append({"code": "unsupported_field_discarded"})
                continue
            eid = accept(claim, block)
            if not eid:
                continue
            key, value = claim.field, claim.value
            if key in {"gpa", "graduation_year"}:
                try:
                    value = int(value) if key == "graduation_year" else float(value)
                    if key == "gpa" and not 0 <= value <= 100:
                        raise ValueError()
                except ValueError:
                    warnings.append({"code": "invalid_numeric_field"})
                    continue
            pointer = "/" + key
            if key in lists:
                if value not in data[key]:
                    data[key].append(value)
                item = pointer + "/" + str(data[key].index(value))
                fields[item] = metadata("extracted", [eid], block["origin"])
                fields[pointer] = metadata(
                    "extracted",
                    sorted(set(fields.get(pointer, {}).get("evidence_ids", []) + [eid])),
                    block["origin"],
                )
            elif key in conflicts:
                fields[pointer]["evidence_ids"].append(eid)
            elif data[key] is not None and data[key] != value:
                conflicts.add(key)
                data[key] = None
                warnings.append({"code": "conflicting_observations", "field": key})
                fields[pointer] = metadata(
                    "unavailable",
                    fields[pointer]["evidence_ids"] + [eid],
                    block["origin"],
                    "conflicting_observations",
                )
            else:
                data[key] = value
                fields[pointer] = metadata("extracted", [eid], block["origin"])
        for record in result.records:
            if record.category not in records:
                warnings.append({"code": "unsupported_category_discarded"})
                continue
            row, row_evidence = {}, {}
            for claim in record.claims:
                if claim.field not in records[record.category]:
                    continue
                eid = accept(claim, block)
                if eid:
                    if claim.field in {"technologies", "topics"}:
                        row.setdefault(claim.field, [])
                        if claim.value not in row[claim.field]:
                            row[claim.field].append(claim.value)
                    else:
                        row[claim.field] = claim.value
                    row_evidence.setdefault(claim.field, []).append(eid)
            anchors = {"title", "name", "institution", "text"}
            if not row.keys() & anchors:
                continue
            if not dates_sane(row.get("start_date"), row.get("end_date")):
                warnings.append({"code": "ambiguous_or_invalid_dates", "category": record.category})
                # Keep the raw source dates; flag rather than fabricate normalized values.
            for key in records[record.category]:
                row.setdefault(key, [] if key in {"technologies", "topics"} else None)
            if row not in data[record.category]:
                data[record.category].append(row)
            idx = data[record.category].index(row)
            base = f"/{record.category}/{idx}"
            ids = sorted({e for es in row_evidence.values() for e in es})
            fields[base] = metadata("extracted", ids, block["origin"])
            for key, value in row.items():
                fields[base + "/" + key] = metadata(
                    "extracted" if value else "not_found", row_evidence.get(key, []), block["origin"]
                )
                if isinstance(value, list):
                    for vi in range(len(value)):
                        fields[f"{base}/{key}/{vi}"] = metadata(
                            "extracted", row_evidence.get(key, []), block["origin"]
                        )
            pointer = "/" + record.category
            fields[pointer] = metadata(
                "extracted",
                sorted(set(fields.get(pointer, {}).get("evidence_ids", []) + ids)),
                block["origin"],
            )

    incomplete = examined != len(blocks)
    absent = "failed" if incomplete else "not_found"
    for key in data:
        fields.setdefault(
            "/" + key,
            metadata(absent, reason="processing_incomplete" if incomplete else "absent_in_examined_material"),
        )
    if incomplete:
        for field in fields.values():
            if field["status"] == "not_found":
                field.update(status="failed", reason_code="processing_incomplete")
    usable = any(v is not None and v != [] for v in data.values())
    state = ("partial" if incomplete else "succeeded") if usable else ("failed" if errors else "succeeded")
    if requested is not None:
        data = {k: data.get(k) for k in requested}
        fields = {p: v for p, v in fields.items() if p.split("/")[1] in requested}
        for k in requested:
            fields.setdefault("/" + k, metadata("unsupported", reason="field_not_supported"))
    referenced = {e for f in fields.values() for e in f["evidence_ids"]}
    return {
        "type": kind,
        "state": state,
        "data": data,
        "fields": fields,
        "evidence": {e: evidence[e] for e in referenced},
        "inputs": sorted({b["input_id"] for b in blocks}),
        "coverage": {
            "blocks_received": len(blocks),
            "blocks_examined": examined,
            "characters_sent": sent,
            "truncated": False,
        },
        "warnings": warnings,
        "errors": errors,
    }

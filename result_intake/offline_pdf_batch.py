"""Offline batch extraction. No network, source writes, or model input."""
import hashlib
from datetime import datetime
from pathlib import Path

def _time(value):
    if type(value) is not str:
        raise ValueError("observation_required")
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("timezone_required")
    return d.isoformat()

def extract_batch(manifest, extractor):
    """extractor(raw_pdf_bytes) -> list of page objects; caller owns adapter."""
    if type(manifest) is not list or not manifest:
        raise ValueError("nonempty_manifest_required")
    seen = {}
    items = []
    for index, row in enumerate(manifest):
        item = {"index": index, "status": "hold"}
        try:
            if type(row) is not dict:
                raise ValueError("manifest_object_required")
            observed = _time(row.get("observed_at"))
            expected = row.get("sha256")
            if (type(expected) is not str or len(expected) != 64 or
                    any(c not in "0123456789abcdef" for c in expected)):
                raise ValueError("sha256_required")
            path = row.get("path")
            if type(path) is not str or not Path(path).is_absolute():
                raise ValueError("absolute_local_path_required")
            if row.get("use_status") != "approved_local_analysis":
                raise ValueError("local_analysis_not_approved")
            data = Path(path).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            item.update({"sha256": digest, "observed_at": observed})
            if digest != expected:
                raise ValueError("source_hash_mismatch")
            if not data.startswith(b"%PDF-"):
                raise ValueError("not_pdf")
            if digest in seen:
                item.update({"status": "duplicate", "duplicate_of": seen[digest]})
            else:
                pages = extractor(data)
                if type(pages) is not list or not pages or any(type(p) is not dict for p in pages):
                    raise ValueError("invalid_page_extraction")
                item.update({"status": "extracted", "pages": pages, "page_count": len(pages)})
                seen[digest] = index
        except (ValueError, OSError, KeyError, IndexError, TypeError, RuntimeError) as exc:
            # Never include source contents or local paths in diagnostics.
            item["reason"] = type(exc).__name__
            if isinstance(exc, ValueError) and str(exc) in {
                "observation_required", "timezone_required", "manifest_object_required",
                "sha256_required", "absolute_local_path_required", "local_analysis_not_approved",
                "source_hash_mismatch", "not_pdf", "invalid_page_extraction"}:
                item["reason"] = str(exc)
        items.append(item)
    return {
        "schema": "offline-pdf-batch-v1",
        "items": items,
        "files": len(items),
        "extracted": sum(x["status"] == "extracted" for x in items),
        "duplicates": sum(x["status"] == "duplicate" for x in items),
        "holds": sum(x["status"] == "hold" for x in items),
        "model_ready": False,
        "prediction_created": False,
        "network_used": False,
    }

def existing_fragment_adapter(module):
    """Reuse reviewed local pdf_text helpers; supports only flat Pages/Kids PDFs.
    Reject nested trees instead of silently dropping pages. No main()/writes.
    Text fragments are not normalized race results.
    """
    import re
    def extract(data):
        module.UNKNOWN.clear()
        objs = module.objects(data)
        roots = [n for n, body in objs.items()
                 if re.search(rb"/Type\s*/Pages\b", body) and b"/Kids" in body]
        if len(roots) != 1:
            raise ValueError("unsupported_page_tree")
        kids = re.search(rb"/Kids\s*\[([^\]]*)\]", objs[roots[0]])
        if not kids:
            raise ValueError("missing_page_children")
        ids = [int(x) for x in re.findall(rb"(\d+)\s+0\s+R", kids.group(1))]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("invalid_page_children")
        count = re.search(rb"/Count\s+(\d+)\b", objs[roots[0]])
        if not count or int(count.group(1)) != len(ids):
            raise ValueError("page_count_mismatch")
        pages = []
        for n in ids:
            body = objs[n]
            if not re.search(rb"/Type\s*/Page\b", body):
                raise ValueError("nested_page_tree")
            fonts = module.page_fonts(objs, body)
            cids = [int(x) for x in re.findall(rb"/Contents\s+(\d+)\s+0\s+R", body)]
            many = re.search(rb"/Contents\s*\[([^\]]*)\]", body)
            if many:
                cids = [int(x) for x in re.findall(rb"(\d+)\s+0\s+R", many.group(1))]
            if not cids:
                raise ValueError("missing_page_contents")
            contents = b"".join(module.stream_of(objs[c]) or b"" for c in cids)
            pieces = module.run(contents, fonts)
            if not pieces:
                raise ValueError("empty_extracted_page")
            pages.append({"page_number": len(pages)+1, "page_obj": n, "pieces": pieces,
                          "text_is_unverified": True})
        return pages
    return extract

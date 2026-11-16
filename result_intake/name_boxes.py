"""Build name-only rectangles from the inspected two-panel PDF layout."""
def boxes(inventory,pages):
    out=[]
    by_page={p['page_number']:p for p in pages}
    for r in inventory['races']:
        page=by_page[r['page']]
        for row in r['rows']:
            parts=[page['pieces'][i] for i in row['name_source_indices']]
            if not parts:raise ValueError('missing_name_provenance')
            out.append(dict(page=r['page'],race_no=r['race_no'],umaban=row['umaban'],
                            name=row['name_candidate'],x=r['panel_origin']+30,
                            y=min(p['y'] for p in parts)-2))
    return out

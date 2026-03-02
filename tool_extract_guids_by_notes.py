import json

def extract_guids_by_notes(json_path, notes_value):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    guids = set()
    
    # Traverse the nested structure of temp.json
    # applications -> {app_name} -> components -> {comp_id} -> datacenters -> {dc_guid}
    apps = data.get('applications', {})
    for app_name, app_data in apps.items():
        components = app_data.get('components', {})
        for comp_id, comp_data in components.items():
            datacenters = comp_data.get('datacenters', {})
            for dc_guid, dc_data in datacenters.items():
                # Check within pdvRun
                pdv_run = dc_data.get('pdvRun', {})
                if pdv_run.get('notes', '').strip() == notes_value.strip():
                    guids.add(dc_guid)
                
                # Check within deployment
                deployment = dc_data.get('deployment', {})
                if deployment.get('notes', '').strip() == notes_value.strip():
                    guids.add(dc_guid)

    return sorted(list(guids))

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python extract_guid_by_notes.py <notes_value>")
        sys.exit(1)

    notes_value = sys.argv[1]
    guids = extract_guids_by_notes('cache/temp.json', notes_value)
    for guid in guids:
        print(guid)
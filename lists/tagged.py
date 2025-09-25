"""Helpers for generating tagged Letterboxd collections."""


def get_lists_with_tag(all_lists, tag):
    if not tag:
        print("No tagged list tag configured.")
        return []

    tagged_lists = []
    print(f"\nFiltering for lists with tag '{tag}':")
    for title, url_suffix, tags in all_lists:
        if tag in tags:
            print(f"- Found tagged list: {title}")
            tagged_lists.append((title, url_suffix))
    return tagged_lists


def generate_tagged_collections(tagged_lists):
    collections = {}

    if not tagged_lists:
        return collections

    print("\nPreparing tagged list collections for config...")
    for title, url_suffix in tagged_lists:
        print(f"- Adding tagged list '{title}' collection placeholder")
        sort_title = f"Z-TAG {title}"

        collections[title] = {
            "letterboxd_list": f"https://letterboxd.com{url_suffix}",
            "collection_order": "custom",
            "sort_title": sort_title,
            "sync_mode": "sync",
            "# visible_library": "true/false # <-- Configure visibility manually",
            "# visible_home": "true/false # <-- Configure visibility manually",
            "# visible_shared": "true/false # <-- Configure visibility manually",
        }

    return collections

"""Order math for the drive-priority stashes: a pruned drive remembers its position in the
virtual full order (current list + stashed drives), so any unsubscribe/resubscribe sequence
puts drives back exactly where they were — stashing the raw list index would go stale as
other prunes shrink the list (bulk unsubscribe stashed everything at 0 and restored reversed)."""


def _virtual_order(drive_ids: list, removed_map: dict) -> list:
    order = [str(d) for d in drive_ids]
    for drive_id, position in sorted(removed_map.items(), key=lambda kv: kv[1]):
        order.insert(min(int(position), len(order)), str(drive_id))
    return order


def stash_drive_position(drive_ids: list, removed_map: dict, drive_id) -> tuple[list, dict, bool]:
    """Remove drive_id from the list, recording its virtual position.
    Returns (drive_ids, removed_map, removed)."""
    sid = str(drive_id)
    if not any(str(d) == sid for d in drive_ids):
        return drive_ids, removed_map, False
    position = _virtual_order(drive_ids, removed_map).index(sid)
    new_map = dict(removed_map)
    new_map[sid] = position
    return [d for d in drive_ids if str(d) != sid], new_map, True


def restore_drive_position(drive_ids: list, removed_map: dict, drive_id) -> tuple[list, dict, bool]:
    """Reinsert drive_id at its stashed virtual position.
    Returns (drive_ids, removed_map, restored)."""
    sid = str(drive_id)
    if sid not in removed_map:
        return drive_ids, removed_map, False
    new_map = {k: v for k, v in removed_map.items() if k != sid}
    if any(str(d) == sid for d in drive_ids):
        return drive_ids, new_map, False

    # Insert before the first current member that follows it in the virtual order
    order = _virtual_order(drive_ids, {**new_map, sid: removed_map[sid]})
    current = {str(d) for d in drive_ids}
    index = 0
    for entry in order:
        if entry == sid:
            break
        if entry in current:
            index += 1
    new_ids = list(drive_ids)
    new_ids.insert(index, drive_id)
    return new_ids, new_map, True

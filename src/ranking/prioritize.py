"""Sorts results list by role priority weight (PRD §9). For v0 this is a sort
order only - it does not affect how hard or how often the system searches
for each role.
"""


def _weight_for(job_title: str, target_roles: list[dict]) -> int:
    if not job_title:
        return 0
    title_lower = job_title.lower()
    best = 0
    for role in target_roles:
        if role["name"].lower() in title_lower:
            best = max(best, role["priority_weight"])
    return best


def sort_by_priority(jobs: list[dict], target_roles: list[dict]) -> list[dict]:
    """Returns jobs sorted by matching target-role weight, highest first.
    Jobs matching no target role are sorted last (weight 0), original
    relative order preserved among ties (stable sort)."""
    return sorted(jobs, key=lambda j: _weight_for(j.get("title"), target_roles), reverse=True)

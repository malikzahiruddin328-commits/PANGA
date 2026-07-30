"""Sorts results list by role priority weight (PRD §9). For v0 this is a sort
order only - it does not affect how hard or how often the system searches
for each role.
"""


def weight_for(job_title: str, target_roles: list[dict]) -> int:
    if not job_title:
        return 0
    title_lower = job_title.lower()
    best = 0
    for role in target_roles:
        if role["name"].lower() in title_lower:
            best = max(best, role["priority_weight"])
    return best

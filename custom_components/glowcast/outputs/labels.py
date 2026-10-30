"""Labels for select options and dropdowns: a name used twice gets a number."""

from collections.abc import Collection, Hashable, Mapping


def unique_labels[K: Hashable](
    names: Mapping[K, str], reserved: Collection[str] = ()
) -> dict[K, str]:
    """{key: label}, sorted by name and then key; a name used twice gets a
    number, " (2)", and a label in `reserved` is never given.

    A select option is a name, and two areas (or groups) may share one.
    Ordered by name and then key, so the labels stay the same.
    """
    labels: dict[K, str] = {}
    used = set(reserved)
    for key, name in sorted(names.items(), key=lambda item: (item[1], item[0])):
        label, number = name, 1
        while label in used:
            number += 1
            label = f"{name} ({number})"
        used.add(label)
        labels[key] = label
    return labels
